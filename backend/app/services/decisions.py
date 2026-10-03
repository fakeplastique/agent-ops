import json
import uuid
from datetime import timedelta
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.errors import (
    AlreadyVoted,
    IllegalTransitionError,
    InvalidInput,
    NotFoundError,
    RoundClosed,
    StaleVersion,
    VoteNotAllowed,
)
from app.domain.hashing import canonical_json, content_hash
from app.domain.receipt import KeyRing, Subject, sign
from app.domain.risk import ACTION_TYPE, effective_risk
from app.domain.rounds import Vote, round_outcome
from app.domain.statuses import Risk, Status
from app.domain.transitions import transition
from app.ports.clock import Clock
from app.services import audit, outbox
from app.services.policies import ensure_fits_plan, resolve_policy
from app.services.usage import consume

TITLE_MAX, DESCRIPTION_MAX, PAYLOAD_MAX = 200, 4096, 65_536  # D21

_AGENT = text(
    "select i.id, i.owner_user_id from agent_sessions s"
    " join agent_identities i on i.organization_id = s.organization_id and i.id = s.agent_identity_id"
    " where s.organization_id = :o and s.id = :s and i.status = 'active'"
)
_MEMBER = text("select 1 from memberships where organization_id = :o and user_id = :u")
# What the CHECK on decision_requests.payload measures: jsonb's own text form, not canonical JSON.
_STORED_SIZE = text("select octet_length(cast(:p as jsonb)::text)")
_INSERT = text(
    "insert into decision_requests (organization_id, title, description, action_type, payload,"
    " risk_level, effective_risk_level, content_hash, requested_by_session_id, requested_by_user_id,"
    " policy_snapshot, voting_deadline_at)"
    " values (:o, :title, :description, :action_type, cast(:payload as jsonb), :risk, :effective,"
    " :hash, :session, :user, cast(:snapshot as jsonb), :deadline) returning *"
)


def _has_nul(value: Any) -> bool:
    # Postgres text and jsonb cannot hold U+0000. Iterative: a deep payload must not hit the recursion limit.
    stack = [value]
    while stack:
        v = stack.pop()
        if isinstance(v, str):
            if "\x00" in v:
                return True
        elif isinstance(v, list):
            stack.extend(v)
        elif isinstance(v, dict):
            stack.extend(v)
            stack.extend(v.values())
    return False


def _check_text(name: str, value: Any, low: int, high: int) -> None:
    ok = isinstance(value, str) and low <= len(value) <= high and "\x00" not in value
    if ok:
        try:
            value.encode("utf-8")  # a lone surrogate would reach asyncpg and fail as a 500
        except UnicodeEncodeError:
            ok = False
    if not ok:
        raise InvalidInput(f"{name} must be a string of {low} to {high} characters")


def _validate(title: Any, description: Any, action_type: Any, payload: Any) -> None:
    _check_text("title", title, 1, TITLE_MAX)
    _check_text("description", description, 0, DESCRIPTION_MAX)
    if not isinstance(action_type, str) or not ACTION_TYPE.fullmatch(action_type):
        raise InvalidInput("action_type must be a lowercase slug")
    if not isinstance(payload, dict) or _has_nul(payload):
        raise InvalidInput("payload must be a JSON object without NUL characters")
    if len(canonical_json(payload)) > PAYLOAD_MAX:
        raise InvalidInput(f"payload exceeds {PAYLOAD_MAX} bytes")


async def create_decision(
    session: AsyncSession,
    organization_id: uuid.UUID,
    clock: Clock,
    *,
    title: str,
    description: str,
    action_type: str,
    payload: dict[str, Any],
    risk_level: Any,
    agent_session_id: uuid.UUID | None = None,
    user_id: uuid.UUID | None = None,
) -> dict[str, Any]:
    """Create a request in `pending_votes`. One transaction: a later failure also undoes the usage count."""
    _validate(title, description, action_type, payload)
    declared = Risk.of(risk_level)
    # Also rejects NaN and lone surrogates; hashes the DECLARED risk (a floor change must not void receipts).
    digest = content_hash(title, action_type, payload, declared)

    payload_json = json.dumps(payload, allow_nan=False)
    if (await session.scalar(_STORED_SIZE, {"p": payload_json})) > PAYLOAD_MAX:
        raise InvalidInput(f"payload exceeds {PAYLOAD_MAX} bytes")

    if agent_session_id is not None and user_id is None:
        agent = (
            await session.execute(_AGENT, {"o": organization_id, "s": agent_session_id})
        ).first()
        if agent is None:  # unknown, another tenant's, or revoked: indistinguishable
            raise NotFoundError
        actor_type, actor_id, owner = "agent", agent.id, agent.owner_user_id
    elif user_id is not None and agent_session_id is None:
        if (await session.execute(_MEMBER, {"o": organization_id, "u": user_id})).first() is None:
            raise NotFoundError
        actor_type, actor_id, owner = "user", user_id, user_id
    else:
        raise InvalidInput("exactly one of agent_session_id and user_id is required")

    await consume(session, organization_id, "requests", clock)  # PlanLimitReached -> 402
    snapshot = await resolve_policy(session, organization_id, owner)
    await ensure_fits_plan(session, organization_id, snapshot)  # PolicyExceedsPlan -> 402
    effective = effective_risk(declared, action_type, snapshot["risk_floors"])
    row = (
        (
            await session.execute(
                _INSERT,
                {
                    "o": organization_id,
                    "title": title,
                    "description": description,
                    "action_type": action_type,
                    "payload": payload_json,
                    "risk": declared.name.lower(),
                    "effective": effective.name.lower(),
                    "hash": digest,
                    "session": agent_session_id,
                    "user": user_id,
                    "snapshot": json.dumps(snapshot),
                    "deadline": clock.now() + timedelta(minutes=snapshot["voting_timeout_minutes"]),
                },
            )
        )
        .mappings()
        .one()
    )
    decision = dict(row)
    summary = {
        "status": decision["status"],
        "version": decision["version"],
        "effective_risk_level": decision["effective_risk_level"],
    }
    await audit.record(
        session, organization_id, "decision_request", decision["id"], "created",
        actor_type, actor_id, summary | {"policy_id": snapshot["policy_id"]},
    )  # fmt: skip
    await outbox.emit(
        session, organization_id, "decision.created",
        summary | {"id": str(decision["id"]), "action_type": action_type},
    )  # fmt: skip
    return decision


_LOCK = text(
    "select id, status, version, content_hash, effective_risk_level, requested_by_session_id,"
    " policy_snapshot from decision_requests where organization_id = :o and id = :r for update"
)
_VOTER = text(
    "select s.agent_identity_id, i.status from agent_sessions s"
    " join agent_identities i on i.organization_id = s.organization_id and i.id = s.agent_identity_id"
    " where s.organization_id = :o and s.id = :s"
)
_IDENTITY_OF = text(
    "select agent_identity_id from agent_sessions where organization_id = :o and id = :s"
)
_ROUND_STATE = text(
    "select count(*) as n, coalesce(bool_or(v.session_id = :s), false) as same_session,"
    " coalesce(bool_or(a.agent_identity_id = :i), false) as same_identity from votes v"
    " join agent_sessions a on a.organization_id = v.organization_id and a.id = v.session_id"
    " where v.organization_id = :o and v.decision_request_id = :r"
    " and v.request_version = :ver and v.round = :rd"
)
_INSERT_VOTE = text(
    "insert into votes (organization_id, decision_request_id, session_id, round, verdict, confidence,"
    " rationale, request_version) values (:o, :r, :s, :rd, :verdict, :confidence, :rationale, :ver)"
    " returning id"
)
_ROUND_VOTES = text(
    "select session_id, verdict, confidence, round from votes where organization_id = :o"
    " and decision_request_id = :r and request_version = :ver and round = :rd order by created_at, id"
)
_SET_STATUS = text(
    "update decision_requests set status = :new, updated_at = :now, decided_at = :decided,"
    " receipt = cast(:receipt as jsonb) where organization_id = :o and id = :r and status = :old"
    " returning id"
)


def _checked_vote(verdict: Any, confidence: Any, rationale: Any, request_version: Any) -> Vote:
    _check_text("rationale", rationale, 0, 4096)
    if (
        isinstance(request_version, bool)
        or not isinstance(request_version, int)
        or request_version < 1
    ):
        raise InvalidInput("request_version must be a positive int")
    try:
        return Vote(verdict, confidence, 1)  # validates verdict and confidence (NaN, range, bool)
    except InvalidInput:
        raise
    except ValueError:
        raise InvalidInput(f"unknown verdict {verdict!r}") from None


async def cast_vote(
    session: AsyncSession,
    organization_id: uuid.UUID,
    clock: Clock,
    *,
    keys: KeyRing,
    decision_request_id: uuid.UUID,
    agent_session_id: uuid.UUID,
    request_version: int,
    verdict: Any,
    confidence: Any,
    rationale: str = "",
) -> dict[str, Any]:
    """Record one vote; the vote that completes the round makes the one transition (and reveals round 1).

    The request row is locked first, so concurrent votes (and, later, the timeout job) are serialized:
    a double vote sees the first, and of two last votes one closes the round and the other gets 409."""
    vote = _checked_vote(verdict, confidence, rationale, request_version)
    o, r = organization_id, decision_request_id
    req = (await session.execute(_LOCK, {"o": o, "r": r})).mappings().first()
    if req is None:
        raise NotFoundError
    if request_version != req["version"]:
        raise StaleVersion
    if req["status"] != "pending_votes":  # round 2 opens with S11's discussion gate
        raise RoundClosed
    snapshot = req["policy_snapshot"]
    quorum = snapshot["quorum_size"]

    voter = (await session.execute(_VOTER, {"o": o, "s": agent_session_id})).first()
    if voter is None:
        raise NotFoundError
    identity, identity_status = voter
    if identity_status != "active":
        raise VoteNotAllowed("agent_inactive")
    if req["requested_by_session_id"] is not None:
        requester = (
            await session.execute(_IDENTITY_OF, {"o": o, "s": req["requested_by_session_id"]})
        ).scalar_one()
        if requester == identity:  # the identity, not just this session
            raise VoteNotAllowed("requester_cannot_vote")

    state = (
        await session.execute(
            _ROUND_STATE,
            {"o": o, "r": r, "ver": req["version"], "rd": 1, "s": agent_session_id, "i": identity},
        )
    ).one()
    if state.same_session or (snapshot["require_distinct_agents"] and state.same_identity):
        raise AlreadyVoted

    vote_id = (
        await session.execute(
            _INSERT_VOTE,
            {
                "o": o, "r": r, "s": agent_session_id, "rd": 1, "ver": req["version"],
                "verdict": vote.verdict.value, "confidence": vote.confidence, "rationale": rationale,
            },
        )
    ).scalar_one()  # fmt: skip
    cast = state.n + 1
    # Round 1 is blind: neither audit nor outbox names a verdict or confidence (nor the rationale, ever).
    common = {"decision_request_id": str(r), "session_id": str(agent_session_id), "round": 1,
              "request_version": req["version"]}  # fmt: skip
    await audit.record(session, o, "vote", vote_id, "cast", "agent", identity, common)
    await outbox.emit(session, o, "vote.cast", common | {"cast": cast, "quorum": quorum})

    status = req["status"]
    if cast == quorum:
        status = await _close_round(session, o, clock, keys, req, snapshot)
    return {"vote_id": vote_id, "round": 1, "cast": cast, "quorum": quorum, "status": status}


async def _close_round(
    session: AsyncSession,
    o: uuid.UUID,
    clock: Clock,
    keys: KeyRing,
    req: Any,
    snapshot: dict[str, Any],
) -> str:
    r, version = req["id"], req["version"]
    rows = (await session.execute(_ROUND_VOTES, {"o": o, "r": r, "ver": version, "rd": 1})).all()
    event = round_outcome(
        [Vote(v.verdict, v.confidence, v.round) for v in rows],
        risk=Risk.of(req["effective_risk_level"]),
        quorum_size=snapshot["quorum_size"],
        threshold=snapshot["confidence_threshold"],
        rounds=snapshot["rounds"],
        round_no=1,
    )
    if event is None:  # called only with a full quorum, so unreachable
        raise IllegalTransitionError("the round is not complete")
    old, new = Status(req["status"]), transition(Status(req["status"]), event)
    now = clock.now()
    receipt = (
        sign(Subject(r, version, req["content_hash"], now, "auto"), keys)
        if new is Status.APPROVED
        else None
    )
    decided = now if new in (Status.APPROVED, Status.REJECTED) else None
    result = await session.execute(
        _SET_STATUS,
        {
            "o": o, "r": r, "old": old.value, "new": new.value, "now": now, "decided": decided,
            "receipt": json.dumps(receipt) if receipt else None,
        },
    )  # fmt: skip
    if result.first() is None:  # the lock makes this unreachable; never double-apply if it is not
        raise IllegalTransitionError("the request changed under the vote")
    revealed = {
        "decision_request_id": str(r), "round": 1, "request_version": version,
        "votes": [{"session_id": str(v.session_id), "verdict": v.verdict, "confidence": v.confidence} for v in rows],
    }  # fmt: skip
    await audit.record(
        session, o, "decision_request", r, "round_revealed", "system", None, revealed
    )
    await outbox.emit(session, o, "round.revealed", revealed)
    change = {"from": old.value, "to": new.value, "version": version}
    await audit.record(
        session,
        o,
        "decision_request",
        r,
        "status_changed",
        "system",
        None,
        change | {"receipt": receipt is not None},
    )
    await outbox.emit(session, o, "decision.status_changed", change | {"id": str(r)})
    return new.value

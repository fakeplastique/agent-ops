import json
import uuid
from datetime import timedelta
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.errors import (
    AlreadyVoted,
    DiscussionRequired,
    Forbidden,
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
from app.domain.transitions import Event, transition
from app.ports.clock import Clock
from app.services import audit, outbox
from app.services.messages import post_system_message
from app.services.policies import ensure_fits_plan, resolve_policy
from app.services.status import change_status
from app.services.steps import open_first_step
from app.services.usage import consume
from app.services.validation import check_text as _check_text

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


def _validate(title: Any, description: Any, action_type: Any, payload: Any) -> None:
    _check_text("title", title, 1, TITLE_MAX)
    _check_text("description", description, 0, DESCRIPTION_MAX)
    if not isinstance(action_type, str) or not ACTION_TYPE.fullmatch(action_type):
        raise InvalidInput("action_type must be a lowercase slug")
    if not isinstance(payload, dict) or _has_nul(payload):
        raise InvalidInput("payload must be a JSON object without NUL characters")
    if len(canonical_json(payload)) > PAYLOAD_MAX:
        raise InvalidInput(f"payload exceeds {PAYLOAD_MAX} bytes")


async def _payload_json(session: AsyncSession, payload: dict[str, Any]) -> str:
    payload_json = json.dumps(payload, allow_nan=False)
    if (await session.scalar(_STORED_SIZE, {"p": payload_json})) > PAYLOAD_MAX:
        raise InvalidInput(f"payload exceeds {PAYLOAD_MAX} bytes")
    return payload_json


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

    payload_json = await _payload_json(session, payload)

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
    " policy_snapshot, voting_deadline_at from decision_requests where organization_id = :o and id = :r for update"
)
_VOTER = text(
    "select s.agent_identity_id, i.status from agent_sessions s"
    " join agent_identities i on i.organization_id = s.organization_id and i.id = s.agent_identity_id"
    " where s.organization_id = :o and s.id = :s"
)
_IDENTITY_OF = text(
    "select agent_identity_id from agent_sessions where organization_id = :o and id = :s"
)
# A vote counts only while its identity is active (I6): a revoked agent's earlier vote is ignored.
# The same-session / same-identity flags look at every vote, so a revoked agent cannot vote twice either.
_ROUND_STATE = text(
    "select count(*) filter (where i.status = 'active') as n,"
    " coalesce(bool_or(v.session_id = :s), false) as same_session,"
    " coalesce(bool_or(a.agent_identity_id = :i), false) as same_identity from votes v"
    " join agent_sessions a on a.organization_id = v.organization_id and a.id = v.session_id"
    " join agent_identities i on i.organization_id = a.organization_id and i.id = a.agent_identity_id"
    " where v.organization_id = :o and v.decision_request_id = :r"
    " and v.request_version = :ver and v.round = :rd"
)
_INSERT_VOTE = text(
    "insert into votes (organization_id, decision_request_id, session_id, round, verdict, confidence,"
    " rationale, request_version) values (:o, :r, :s, :rd, :verdict, :confidence, :rationale, :ver)"
    " returning id"
)
_ROUND_VOTES = text(
    "select v.session_id, v.verdict, v.confidence, v.round from votes v"
    " join agent_sessions a on a.organization_id = v.organization_id and a.id = v.session_id"
    " join agent_identities i on i.organization_id = a.organization_id and i.id = a.agent_identity_id"
    " where v.organization_id = :o and v.decision_request_id = :r and v.request_version = :ver"
    " and v.round = :rd and i.status = 'active' order by v.created_at, v.id"
)
_DISCUSSED = text(
    "select exists (select 1 from messages where organization_id = :o and decision_request_id = :r"
    " and request_version = :v and author_type <> 'system')"
)
_SET_DEADLINE = text(
    "update decision_requests set voting_deadline_at = :d where organization_id = :o and id = :r"
)
_REVEAL = text(
    "update votes set revealed_at = :now where organization_id = :o and decision_request_id = :r"
    " and request_version = :ver and round = 1"
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
        raise InvalidInput(f"unknown verdict {str(verdict)[:20]!r}") from None


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
    if req["status"] not in ("pending_votes", "pending_discussion"):
        raise RoundClosed
    if (
        clock.now() >= req["voting_deadline_at"]
    ):  # rule 7: past the deadline humans take over, never auto-approve
        raise RoundClosed
    rd = 1 if req["status"] == "pending_votes" else 2
    if rd == 2 and not await session.scalar(_DISCUSSED, {"o": o, "r": r, "v": req["version"]}):
        # Round 2 follows a discussion. (The plan's "or the deadline passed" branch is the escalation
        # above: once the timer runs out humans take over, so no vote can arrive after it.)
        raise DiscussionRequired
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
            {"o": o, "r": r, "ver": req["version"], "rd": rd, "s": agent_session_id, "i": identity},
        )
    ).one()
    if state.same_session or (snapshot["require_distinct_agents"] and state.same_identity):
        raise AlreadyVoted

    vote_id = (
        await session.execute(
            _INSERT_VOTE,
            {
                "o": o, "r": r, "s": agent_session_id, "rd": rd, "ver": req["version"],
                "verdict": vote.verdict.value, "confidence": vote.confidence, "rationale": rationale,
            },
        )
    ).scalar_one()  # fmt: skip
    cast = state.n + 1
    # Round 1 is blind: neither audit nor outbox names a verdict or confidence (nor the rationale, ever).
    # Round 2 follows an open discussion and is not blind.
    common = {"decision_request_id": str(r), "session_id": str(agent_session_id), "round": rd,
              "request_version": req["version"]}  # fmt: skip
    if rd == 2:
        common |= {"verdict": vote.verdict.value, "confidence": vote.confidence}
    await audit.record(session, o, "vote", vote_id, "cast", "agent", identity, common)
    await outbox.emit(session, o, "vote.cast", common | {"cast": cast, "quorum": quorum})

    status = req["status"]
    if cast == quorum:
        status = await _close_round(session, o, clock, keys, req, snapshot, rd)
    return {"vote_id": vote_id, "round": rd, "cast": cast, "quorum": quorum, "status": status}


async def _close_round(
    session: AsyncSession,
    o: uuid.UUID,
    clock: Clock,
    keys: KeyRing,
    req: Any,
    snapshot: dict[str, Any],
    rd: int,
) -> str:
    r, version = req["id"], req["version"]
    rows = (await session.execute(_ROUND_VOTES, {"o": o, "r": r, "ver": version, "rd": rd})).all()
    event = round_outcome(
        [Vote(v.verdict, v.confidence, v.round) for v in rows],
        risk=Risk.of(req["effective_risk_level"]),
        quorum_size=snapshot["quorum_size"],
        threshold=snapshot["confidence_threshold"],
        rounds=snapshot["rounds"],
        round_no=rd,
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
    if rd == 1:  # the blind round is revealed by closing; round 2 was never hidden
        await session.execute(_REVEAL, {"o": o, "r": r, "ver": version, "now": now})
        revealed = {
            "decision_request_id": str(r), "round": 1, "request_version": version,
            "votes": [{"session_id": str(v.session_id), "verdict": v.verdict, "confidence": v.confidence} for v in rows],
        }  # fmt: skip
        await audit.record(
            session, o, "decision_request", r, "round_revealed", "system", None, revealed
        )
        await outbox.emit(session, o, "round.revealed", revealed)
    await change_status(session, o, r, version, old, new, now, receipt, "system", None)
    if new is Status.PENDING_APPROVAL:
        await open_first_step(session, o, r, version, snapshot, now)
    if new is Status.PENDING_DISCUSSION:  # the same timer runs again from entry to the discussion
        await session.execute(
            _SET_DEADLINE,
            {"o": o, "r": r, "d": now + timedelta(minutes=snapshot["voting_timeout_minutes"])},
        )
    return new.value


_REQUEST_FOR_UPDATE = text(
    "select id, status, version, title, description, action_type, payload, risk_level,"
    " requested_by_session_id from decision_requests where organization_id = :o and id = :r for update"
)
_IDENTITY_OWNER = text(
    "select i.id, i.status, i.owner_user_id from agent_sessions s join agent_identities i"
    " on i.organization_id = s.organization_id and i.id = s.agent_identity_id"
    " where s.organization_id = :o and s.id = :s"
)
_ROLE = text("select role from memberships where organization_id = :o and user_id = :u")
_REVISE = text(
    "update decision_requests set title = :title, description = :description, action_type = :action_type,"
    " payload = cast(:payload as jsonb), risk_level = :risk, effective_risk_level = :effective,"
    " content_hash = :hash, version = version + 1, policy_snapshot = cast(:snapshot as jsonb),"
    " voting_deadline_at = :deadline, updated_at = :now where organization_id = :o and id = :r"
    " and version = :v returning version"
)
_SUPERSEDE = text(
    "update approval_steps set status = 'superseded' where organization_id = :o"
    " and decision_request_id = :r and status = 'pending'"
)


def _cap(value: Any, n: int = 60) -> str:
    text_ = str(value)
    return text_ if len(text_) <= n else text_[: n - 1] + "…"


def _diff_body(version: int, old: Any, new: dict[str, Any], fields: list[str]) -> str:
    parts = []
    for f in fields:
        if f == "payload":  # never copy the payload into the thread: sizes only
            parts.append(
                f"payload changed ({len(json.dumps(old[f]))} -> {len(json.dumps(new[f]))} bytes)"
            )
        else:
            parts.append(f"{f}: {_cap(old[f])!r} -> {_cap(new[f])!r}")
    return f"Revised to version {version}: " + "; ".join(parts)


async def _requester_identity(
    session: AsyncSession, o: uuid.UUID, agent_session_id: uuid.UUID, req: Any
) -> Any:
    """The caller's agent identity, if it is active and is the identity that made this request."""
    me = (await session.execute(_IDENTITY_OWNER, {"o": o, "s": agent_session_id})).first()
    if me is None:
        raise NotFoundError
    if me.status != "active":
        raise Forbidden("this agent identity is not active")
    if req["requested_by_session_id"] is None:
        raise Forbidden("only the requesting agent identity may do this")
    requester = (
        await session.execute(_IDENTITY_OF, {"o": o, "s": req["requested_by_session_id"]})
    ).scalar_one()
    if requester != me.id:  # the identity, not the session: a new session of the same agent may act
        raise Forbidden("only the requesting agent identity may do this")
    return me


async def revise_decision(
    session: AsyncSession,
    organization_id: uuid.UUID,
    clock: Clock,
    *,
    decision_request_id: uuid.UUID,
    agent_session_id: uuid.UUID,
    title: str | None = None,
    description: str | None = None,
    action_type: str | None = None,
    payload: dict[str, Any] | None = None,
    risk_level: Any = None,
) -> dict[str, Any]:
    """The original agent identity changes a pending request: version + 1, old votes and steps stop counting.

    Votes stay in history but every count is version-scoped; pending steps are superseded; the policy is
    re-resolved (and must still fit the plan); the timer restarts. Revisions do not count against the plan."""
    o, r = organization_id, decision_request_id
    req = (await session.execute(_REQUEST_FOR_UPDATE, {"o": o, "r": r})).mappings().first()
    if req is None:
        raise NotFoundError
    me = await _requester_identity(session, o, agent_session_id, req)
    old_status = Status(req["status"])
    new_status = transition(
        old_status, Event.REVISE
    )  # terminal requests raise IllegalTransitionError

    old = {k: req[k] for k in ("title", "description", "action_type", "payload")}
    old["risk_level"] = req["risk_level"]
    new = {
        "title": req["title"] if title is None else title,
        "description": req["description"] if description is None else description,
        "action_type": req["action_type"] if action_type is None else action_type,
        "payload": req["payload"] if payload is None else payload,
        "risk_level": req["risk_level"] if risk_level is None else Risk.of(risk_level).name.lower(),
    }
    _validate(new["title"], new["description"], new["action_type"], new["payload"])
    # Payloads compare as the hash sees them (1 == 1.0, but True != 1), not as Python dicts do.
    same = {
        f: (canonical_json(new[f]) == canonical_json(old[f]))
        if f == "payload"
        else new[f] == old[f]
        for f in new
    }
    fields = [f for f in new if not same[f]]
    if not fields:
        raise InvalidInput("nothing to revise")
    declared = Risk.of(new["risk_level"])
    digest = content_hash(new["title"], new["action_type"], new["payload"], declared)
    payload_json = await _payload_json(session, new["payload"])
    snapshot = await resolve_policy(session, o, me.owner_user_id)
    await ensure_fits_plan(session, o, snapshot)
    now = clock.now()
    revised = (
        await session.execute(
            _REVISE,
            {
                "o": o,
                "r": r,
                "v": req["version"],
                "title": new["title"],
                "description": new["description"],
                "action_type": new["action_type"],
                "payload": payload_json,
                "risk": declared.name.lower(),
                "effective": effective_risk(
                    declared, new["action_type"], snapshot["risk_floors"]
                ).name.lower(),
                "hash": digest,
                "snapshot": json.dumps(snapshot),
                "now": now,
                "deadline": now + timedelta(minutes=snapshot["voting_timeout_minutes"]),
            },
        )
    ).scalar_one()
    await session.execute(_SUPERSEDE, {"o": o, "r": r})
    if old_status is not new_status:
        await change_status(
            session, o, r, revised, old_status, new_status, now, None, "agent", me.id
        )
    await post_system_message(session, o, r, revised, _diff_body(revised, old, new, fields))
    data = {
        "fields": fields,
        "from_version": req["version"],
        "version": revised,
        "from_status": old_status.value,
    }
    await audit.record(session, o, "decision_request", r, "revised", "agent", me.id, data)
    await outbox.emit(session, o, "decision.revised", data | {"id": str(r)})
    return {"id": r, "version": revised, "status": new_status.value, "fields": fields}


async def withdraw_decision(
    session: AsyncSession,
    organization_id: uuid.UUID,
    clock: Clock,
    *,
    decision_request_id: uuid.UUID,
    agent_session_id: uuid.UUID | None = None,
    user_id: uuid.UUID | None = None,
) -> dict[str, Any]:
    """The original agent identity, or an org_admin, cancels a request that is not yet decided."""
    o, r = organization_id, decision_request_id
    req = (await session.execute(_REQUEST_FOR_UPDATE, {"o": o, "r": r})).mappings().first()
    if req is None:
        raise NotFoundError
    if agent_session_id is not None and user_id is None:
        actor_type, actor_id = (
            "agent",
            (await _requester_identity(session, o, agent_session_id, req)).id,
        )
    elif user_id is not None and agent_session_id is None:
        role = (await session.execute(_ROLE, {"o": o, "u": user_id})).scalar()
        if role is None:
            raise NotFoundError
        if role != "org_admin":
            raise Forbidden("only an org_admin or the requesting agent may withdraw")
        actor_type, actor_id = "user", user_id
    else:
        raise InvalidInput("exactly one of agent_session_id and user_id is required")
    old = Status(req["status"])
    new = transition(old, Event.WITHDRAW)  # a decided request is a 409
    await session.execute(_SUPERSEDE, {"o": o, "r": r})  # an open step must not outlive its request
    await change_status(
        session, o, r, req["version"], old, new, clock.now(), None, actor_type, actor_id
    )
    return {"id": r, "status": new.value}

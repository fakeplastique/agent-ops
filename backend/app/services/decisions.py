import json
import uuid
from datetime import timedelta
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.errors import InvalidInput, NotFoundError
from app.domain.hashing import canonical_json, content_hash
from app.domain.risk import ACTION_TYPE, effective_risk
from app.domain.statuses import Risk
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

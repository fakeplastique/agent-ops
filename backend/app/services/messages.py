import uuid
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.errors import DiscussionClosed, Forbidden, InvalidInput, NotFoundError
from app.ports.clock import Clock
from app.services import audit, outbox
from app.services.validation import check_text

BODY_MAX = 4096
_REQUEST = text(
    "select id, status, version from decision_requests where organization_id = :o and id = :r for share"
)
_AGENT = text(
    "select i.id, i.status from agent_sessions s join agent_identities i"
    " on i.organization_id = s.organization_id and i.id = s.agent_identity_id"
    " where s.organization_id = :o and s.id = :s"
)
_MEMBER = text("select 1 from memberships where organization_id = :o and user_id = :u")
_INSERT = text(
    "insert into messages (organization_id, decision_request_id, request_version, author_type, author_id, body)"
    " values (:o, :r, :v, :t, :a, :b) returning id"
)


async def _insert(
    session: AsyncSession, o: uuid.UUID, request_id: uuid.UUID, version: int,
    author_type: str, author_id: uuid.UUID | None, body: str,
) -> uuid.UUID:  # fmt: skip
    message_id = (
        await session.execute(
            _INSERT,
            {"o": o, "r": request_id, "v": version, "t": author_type, "a": author_id, "b": body},
        )
    ).scalar_one()
    # The body is data for the read path only; events and audit name who and where, never what.
    data = {
        "decision_request_id": str(request_id),
        "request_version": version,
        "author_type": author_type,
    }
    await audit.record(session, o, "message", message_id, "posted", author_type, author_id, data)
    await outbox.emit(session, o, "message.posted", data | {"id": str(message_id)})
    return message_id


async def post_system_message(
    session: AsyncSession, o: uuid.UUID, request_id: uuid.UUID, version: int, body: str
) -> uuid.UUID:
    """The thread of a request in pending_votes holds these only (no agent or user chat before the reveal)."""
    return await _insert(session, o, request_id, version, "system", None, body)


async def post_message(
    session: AsyncSession,
    organization_id: uuid.UUID,
    clock: Clock,
    *,
    decision_request_id: uuid.UUID,
    body: Any,
    agent_session_id: uuid.UUID | None = None,
    user_id: uuid.UUID | None = None,
) -> dict[str, Any]:
    """An agent of the organization or a member posts, from pending_discussion through pending_approval."""
    o = organization_id
    check_text("body", body, 1, BODY_MAX)
    req = (await session.execute(_REQUEST, {"o": o, "r": decision_request_id})).mappings().first()
    if req is None:
        raise NotFoundError
    if req["status"] not in ("pending_discussion", "pending_approval"):
        raise DiscussionClosed  # system messages only before the reveal; closed once decided
    if agent_session_id is not None and user_id is None:
        agent = (await session.execute(_AGENT, {"o": o, "s": agent_session_id})).first()
        if agent is None:
            raise NotFoundError
        if agent.status != "active":
            raise Forbidden("this agent identity is not active")
        author_type, author_id = "agent", agent.id
    elif user_id is not None and agent_session_id is None:
        if (await session.execute(_MEMBER, {"o": o, "u": user_id})).first() is None:
            raise NotFoundError
        author_type, author_id = "user", user_id
    else:
        raise InvalidInput("exactly one of agent_session_id and user_id is required")
    message_id = await _insert(session, o, req["id"], req["version"], author_type, author_id, body)
    return {"id": message_id, "request_version": req["version"], "author_type": author_type}

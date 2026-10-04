import uuid
from datetime import timedelta
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.errors import NotFoundError, SessionLimit
from app.ports.clock import Clock
from app.services.validation import check_text

LABEL_MAX = 128
NEW_SESSIONS_PER_HOUR = 10  # D12
_IDENTITY = text("select status from agent_identities where organization_id = :o and id = :i")
# One transaction at a time per identity: the cap is checked and the row inserted under this lock.
_LOCK = text("select pg_advisory_xact_lock(hashtextextended(cast(:i as text), 0))")
_FIND = text(
    "update agent_sessions set last_seen_at = :now where organization_id = :o and agent_identity_id = :i"
    " and session_label = :l returning id"
)
_RECENT = text(
    "select count(*) from agent_sessions where organization_id = :o and agent_identity_id = :i"
    " and started_at > :since"
)
_INSERT = text(
    "insert into agent_sessions (organization_id, agent_identity_id, session_label, started_at, last_seen_at)"
    " values (:o, :i, :l, :now, :now) returning id"
)


async def get_or_create_session(
    session: AsyncSession,
    organization_id: uuid.UUID,
    agent_identity_id: uuid.UUID,
    clock: Clock,
    *,
    header_label: Any = None,
    argument_label: Any = None,
) -> tuple[uuid.UUID, bool]:
    """The (identity, label) session, created on first use: the label is the MCP session header, else the
    `session_label` argument. A new session each call would inflate a quorum, so one label is one session,
    and an identity may open at most 10 new ones per hour (D12). Returns (session id, created)."""
    o, i = organization_id, agent_identity_id
    label = header_label or argument_label
    check_text("session label", label, 1, LABEL_MAX)
    if (await session.execute(_IDENTITY, {"o": o, "i": i})).scalar() != "active":
        raise NotFoundError
    await session.execute(_LOCK, {"i": str(i)})
    now = clock.now()
    existing = (await session.execute(_FIND, {"o": o, "i": i, "l": label, "now": now})).scalar()
    if existing is not None:
        return existing, False
    recent = await session.scalar(_RECENT, {"o": o, "i": i, "since": now - timedelta(hours=1)})
    if recent >= NEW_SESSIONS_PER_HOUR:
        raise SessionLimit
    created = (
        await session.execute(_INSERT, {"o": o, "i": i, "l": label, "now": now})
    ).scalar_one()
    return created, True

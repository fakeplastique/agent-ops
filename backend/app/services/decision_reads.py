import uuid
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.errors import NotFoundError
from app.domain.statuses import TERMINAL, Status
from app.services.validation import check_text

# Explicit columns: the policy snapshot, receipt, search vector and session ids never leave this module.
_COLUMNS = (
    "d.id, d.title, d.description, d.action_type, d.payload, d.risk_level, d.effective_risk_level,"
    " d.status, d.version, d.content_hash, a.agent_identity_id as requested_by_agent_id,"
    " d.requested_by_user_id, d.voting_deadline_at, d.escalation_reason, d.decided_at,"
    " d.created_at, d.updated_at"
)
_FROM = (
    " from decision_requests d left join agent_sessions a"
    " on a.organization_id = d.organization_id and a.id = d.requested_by_session_id"
)
_OPEN = ", ".join(f"'{s.value}'" for s in Status if s not in TERMINAL)  # undecided, from the enum
# ponytail: `q` is an ILIKE scan within one org; add a trigram index if per-org lists grow large.
_FILTER = (
    " where d.organization_id = :o and (cast(:status as text) is null or d.status = :status)"
    " and (cast(:risk as text) is null or d.effective_risk_level = :risk)"
    " and (cast(:agent as uuid) is null or a.agent_identity_id = :agent)"
    " and (cast(:q as text) is null or d.title ilike :q escape '\\')"
    " and (cast(:user as uuid) is null or d.requested_by_user_id = :user)"
    f" and (not cast(:open as boolean) or d.status in ({_OPEN}))"
)
# Allow-listed sort keys; `id` breaks ties in the same direction so pages never overlap.
SORTS = {
    f"{sign}{col}": f"d.{col} {direction}, d.id {direction}"
    for col in ("created_at", "updated_at", "title")
    for sign, direction in (("", "asc"), ("-", "desc"))
}
_ONE = text(f"select {_COLUMNS}{_FROM} where d.organization_id = :o and d.id = :r")
_SESSION = text(
    "select id from agent_sessions where organization_id = :o and agent_identity_id = :i"
    " and session_label = :l"
)
_MESSAGES = text(
    "select id, author_type, author_id, body, request_version, created_at from messages"
    " where organization_id = :o and decision_request_id = :r order by created_at, id"
    " limit :limit offset :skip"
)
_MESSAGE_COUNT = text(
    "select count(*) from messages where organization_id = :o and decision_request_id = :r"
)


async def get_decision(session: AsyncSession, o: uuid.UUID, r: uuid.UUID) -> dict[str, Any]:
    row = (await session.execute(_ONE, {"o": o, "r": r})).mappings().first()
    if row is None:
        raise NotFoundError
    return dict(row)


def _like(q: str | None) -> str | None:
    if q is None:
        return None
    # min 0: a search term may be blank ("" matches all); NUL or a lone surrogate would be a 500
    check_text("q", q, 0, 200)
    return "%" + q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


async def list_decisions(
    session: AsyncSession,
    o: uuid.UUID,
    *,
    status: str | None,
    risk_level: str | None,
    agent_id: uuid.UUID | None,
    q: str | None,
    sort: str,
    skip: int,
    limit: int,
    user_id: uuid.UUID | None = None,
    open_only: bool = False,
) -> tuple[list[dict[str, Any]], int]:
    """Filters combine with AND; `risk_level` matches the effective risk (what routes the request).
    `open_only` keeps the undecided ones (MCP list_open_decisions), `user_id` a person's own requests."""
    args = {"o": o, "status": status, "risk": risk_level, "agent": agent_id, "q": _like(q)}
    args |= {"user": user_id, "open": open_only}
    total = await session.scalar(text(f"select count(*){_FROM}{_FILTER}"), args)
    page = text(
        f"select {_COLUMNS}{_FROM}{_FILTER} order by {SORTS[sort]} limit :limit offset :skip"
    )
    rows = (await session.execute(page, args | {"limit": limit, "skip": skip})).mappings().all()
    return [dict(r) for r in rows], int(total or 0)


async def find_session(
    session: AsyncSession, o: uuid.UUID, identity: uuid.UUID, label: str
) -> uuid.UUID | None:
    """The agent's existing session for this label, without creating one (reads have no side effects)."""
    return (await session.execute(_SESSION, {"o": o, "i": identity, "l": label})).scalar()


async def list_messages(
    session: AsyncSession, o: uuid.UUID, r: uuid.UUID, skip: int, limit: int
) -> tuple[list[dict[str, Any]], int]:
    await get_decision(session, o, r)  # 404 for a missing or foreign request, not an empty thread
    args = {"o": o, "r": r}
    rows = (await session.execute(_MESSAGES, args | {"skip": skip, "limit": limit})).mappings()
    total = await session.scalar(_MESSAGE_COUNT, args)
    return [dict(m) for m in rows.all()], int(total or 0)

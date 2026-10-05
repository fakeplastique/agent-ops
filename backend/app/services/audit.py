import uuid
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import AuditLog

# A request's trail also holds its votes, steps and messages, which name it in `data`.
_WHERE = (
    " from audit_logs where organization_id = :o and (cast(:r as uuid) is null"
    " or entity_id = :r or data->>'decision_request_id' = cast(:r as text))"
)
_LIST = text(
    "select id, entity_type, entity_id, action, actor_type, actor_id, data, created_at"
    f"{_WHERE} order by created_at, id limit :limit offset :skip"
)
_COUNT = text(f"select count(*){_WHERE}")


async def record(
    session: AsyncSession,
    organization_id: uuid.UUID,
    entity_type: str,
    entity_id: uuid.UUID,
    action: str,
    actor_type: str,
    actor_id: uuid.UUID | None = None,
    data: dict[str, Any] | None = None,
) -> None:
    # Adds to the caller's session: commits or rolls back with the state change, never alone.
    session.add(
        AuditLog(
            organization_id=organization_id,
            entity_type=entity_type,
            entity_id=entity_id,
            action=action,
            actor_type=actor_type,
            actor_id=actor_id,
            data=data or {},
        )
    )
    await session.flush()


async def list_entries(
    session: AsyncSession,
    o: uuid.UUID,
    decision_request_id: uuid.UUID | None,
    skip: int,
    limit: int,
) -> tuple[list[dict[str, Any]], int]:
    """The organization's audit trail, oldest first. Round-1 vote rows never held a verdict to show."""
    p = {"o": o, "r": decision_request_id}
    rows = await session.execute(_LIST, p | {"skip": skip, "limit": limit})
    total = (await session.execute(_COUNT, p)).scalar_one()
    return [dict(r) for r in rows.mappings()], total

import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import AuditLog


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

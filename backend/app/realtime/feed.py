import uuid
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.db.session import tenant_session

# seq order is commit order (the counter row lock, services/outbox.py), so `seq > after` never skips a
# lower seq that commits later: it cannot exist once a higher one is visible.
_AFTER = text(
    "select id, seq, type, payload, created_at from outbox_events"
    " where organization_id = :o and seq > :after order by seq limit :limit"
)


async def read_after(
    engine: AsyncEngine, org: uuid.UUID, after: int, limit: int
) -> list[dict[str, Any]]:
    """The organization's raw events after the cursor, in seq order (one short transaction)."""
    async with tenant_session(engine, org) as s:
        rows = await s.execute(_AFTER, {"o": org, "after": after, "limit": limit})
        return [dict(r) for r in rows.mappings()]

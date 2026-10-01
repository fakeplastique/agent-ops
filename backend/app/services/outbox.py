import uuid
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import OutboxEvent

# The upsert takes the counter row's lock, held until commit: the next writer in this org
# waits, so seq order equals commit order (a DB sequence would hand out numbers before commit).
_NEXT_SEQ = text(
    "insert into org_event_counters (organization_id, seq) values (:o, 1)"
    " on conflict (organization_id) do update set seq = org_event_counters.seq + 1"
    " returning seq"
)


async def emit(
    session: AsyncSession, organization_id: uuid.UUID, type: str, payload: dict[str, Any]
) -> int:
    seq = (await session.execute(_NEXT_SEQ, {"o": organization_id})).scalar_one()
    session.add(OutboxEvent(organization_id=organization_id, seq=seq, type=type, payload=payload))
    await session.flush()
    return seq

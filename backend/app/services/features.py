import uuid

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.services import cache

# The only place that decides a flag. An override (either way) beats the plan; anything unknown is off.
_ENABLED = text(
    "select coalesce("
    " (select enabled from organization_feature_overrides where organization_id = :o and flag_key = :f),"
    " exists (select 1 from organizations o join plan_features pf on pf.plan_key = o.plan_key"
    "         where o.id = :o and pf.flag_key = :f))"
)


async def is_enabled(session: AsyncSession, organization_id: uuid.UUID, flag_key: str) -> bool:
    async def load() -> bool:
        return bool(await session.scalar(_ENABLED, {"o": organization_id, "f": flag_key}))

    return await cache.get_or_load(session, organization_id, "flags", flag_key, load)

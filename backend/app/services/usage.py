import uuid
from datetime import UTC

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.errors import PlanLimitReached
from app.ports.clock import Clock

_UPGRADE = {"free": "Pro", "pro": "Enterprise"}
_LIMIT = text(
    "select p.requests_per_month, p.key from organizations o join plans p on p.key = o.plan_key"
    " where o.id = :o"
)
# ON CONFLICT DO UPDATE locks the row and re-checks the WHERE after the lock: two callers racing
# for the last slot cannot both pass. No row returned = over the limit.
_CONSUME = text(
    "insert into usage_counters (organization_id, metric, period, value) values (:o, :m, :p, 1)"
    " on conflict (organization_id, metric, period) do update set value = usage_counters.value + 1"
    " where cast(:lim as int) is null or usage_counters.value < cast(:lim as int)"
    " returning value"
)


async def consume(
    session: AsyncSession, organization_id: uuid.UUID, metric: str, clock: Clock
) -> int:
    """Take one unit for this UTC month or raise PlanLimitReached (also for an unknown org)."""
    row = (await session.execute(_LIMIT, {"o": organization_id})).first()
    if row is None or (
        row[0] is not None and row[0] < 1
    ):  # a 0 limit would let the first insert slip in
        raise PlanLimitReached
    period = clock.now().astimezone(UTC).strftime("%Y-%m")
    args = {"o": organization_id, "m": metric, "p": period, "lim": row[0]}
    value = (await session.execute(_CONSUME, args)).scalar_one_or_none()
    if value is None:  # the spec's actionable text: what is spent and what lifts it
        up = _UPGRADE.get(row[1], "a larger plan")
        raise PlanLimitReached(
            f"quota {row[0]}/{row[0]} {metric} used this month on the {row[1]} plan: upgrade to {up}"
            " or wait for next month"
        )
    return value

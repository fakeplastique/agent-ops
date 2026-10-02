import uuid
from datetime import UTC, datetime

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine


class FakeClock:
    def __init__(self, now: datetime) -> None:
        self._now = now

    def now(self) -> datetime:
        return self._now


def utc(*args: int) -> datetime:
    return datetime(*args, tzinfo=UTC)


async def make_org(owner: AsyncEngine, plan_key: str = "free") -> uuid.UUID:
    # The owner skips RLS, so a test can plant any tenant without a tenant context.
    org = uuid.uuid4()
    async with owner.begin() as c:
        await c.execute(
            text("insert into organizations (id, name, slug, plan_key) values (:i, 'n', :s, :p)"),
            {"i": org, "s": f"o-{org}", "p": plan_key},
        )
    return org


async def set_usage(owner: AsyncEngine, org: uuid.UUID, period: str, value: int) -> None:
    async with owner.begin() as c:
        await c.execute(
            text(
                "insert into usage_counters (organization_id, metric, period, value)"
                " values (:o, 'requests', :p, :v)"
            ),
            {"o": org, "p": period, "v": value},
        )

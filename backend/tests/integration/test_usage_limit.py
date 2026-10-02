import asyncio
import uuid

import pytest
from sqlalchemy import text

from app.db.session import tenant_session
from app.domain.errors import PlanLimitReached
from app.services.usage import consume

from .helpers import FakeClock, make_org, set_usage, utc

pytestmark = pytest.mark.anyio

JAN = FakeClock(utc(2026, 1, 15))


async def _try(engine, org, clock) -> bool:
    try:
        async with tenant_session(engine, org) as s:
            await consume(s, org, "requests", clock)
        return True
    except PlanLimitReached:
        return False


async def _value(owner, org, period) -> int | None:
    async with owner.connect() as c:
        return await c.scalar(
            text("select value from usage_counters where organization_id = :o and period = :p"),
            {"o": org, "p": period},
        )


async def test_last_slot_two_concurrent(app_engine, owner_engine):
    org = await make_org(owner_engine, "free")
    await set_usage(owner_engine, org, "2026-01", 49)
    results = await asyncio.gather(*(_try(app_engine, org, JAN) for _ in range(2)))
    assert sorted(results) == [False, True]  # catches: read-then-write letting both through
    assert await _value(owner_engine, org, "2026-01") == 50


async def test_over_limit_raises_and_does_not_burn_the_counter(app_engine, owner_engine):
    org = await make_org(owner_engine, "free")
    await set_usage(owner_engine, org, "2026-01", 50)
    assert not await _try(app_engine, org, JAN)
    assert await _value(owner_engine, org, "2026-01") == 50


async def test_limit_is_per_org_and_per_month(app_engine, owner_engine):
    a, b = await make_org(owner_engine, "free"), await make_org(owner_engine, "free")
    await set_usage(owner_engine, a, "2026-01", 50)
    assert await _try(app_engine, b, JAN)  # catches: a counter shared across orgs
    last_second = FakeClock(utc(2026, 1, 31, 23, 59, 59))
    first_second = FakeClock(utc(2026, 2, 1, 0, 0, 0))
    assert not await _try(app_engine, a, last_second)
    assert await _try(app_engine, a, first_second)  # catches: local-time or sticky month
    assert await _value(owner_engine, a, "2026-02") == 1


async def test_unlimited_plan_never_blocks(app_engine, owner_engine):
    org = await make_org(owner_engine, "enterprise")
    await set_usage(owner_engine, org, "2026-01", 10**6)
    assert await _try(app_engine, org, JAN)  # catches: NULL limit treated as 0
    assert await _value(owner_engine, org, "2026-01") == 10**6 + 1


async def test_unknown_organization_cannot_consume(app_engine):
    assert not await _try(app_engine, uuid.uuid4(), JAN)

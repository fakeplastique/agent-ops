import asyncio

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.db.session import tenant_session
from app.services import outbox

pytestmark = pytest.mark.anyio


async def _waiting_on_counter(owner: AsyncEngine) -> bool:
    # Scoped to this database: parallel sessions run the same test elsewhere.
    q = (
        "select count(*) from pg_stat_activity where datname = current_database()"
        " and wait_event_type = 'Lock' and query like '%org_event_counters%'"
    )
    for _ in range(100):
        async with owner.connect() as c:
            if (await c.scalar(text(q))) or 0:
                return True
        await asyncio.sleep(0.05)
    return False


async def test_seq_order_equals_commit_order(
    app_engine: AsyncEngine, owner_engine: AsyncEngine, org_a
):
    release = asyncio.Event()
    first_has_seq = asyncio.Event()

    async def first() -> int:
        async with tenant_session(app_engine, org_a) as s:
            seq = await outbox.emit(s, org_a, "first", {})
            first_has_seq.set()
            await release.wait()  # commit only after the second writer is stuck behind us
            return seq

    async def second() -> int:
        await first_has_seq.wait()
        async with tenant_session(app_engine, org_a) as s:
            return await outbox.emit(s, org_a, "second", {})

    t1, t2 = asyncio.create_task(first()), asyncio.create_task(second())
    blocked = await _waiting_on_counter(owner_engine)
    release.set()
    seqs = await asyncio.gather(t1, t2)
    assert blocked, "second writer got a seq without waiting for the first to commit"
    assert seqs == [1, 2]
    # Replay from every cursor sees the commit order.
    async with tenant_session(app_engine, org_a) as s:
        for cursor, expected in [(0, ["first", "second"]), (1, ["second"]), (2, [])]:
            rows = await s.execute(
                text("select type from outbox_events where seq > :c order by seq"), {"c": cursor}
            )
            assert [r[0] for r in rows] == expected


async def test_seq_gapless_per_org_and_independent_across_orgs(
    app_engine: AsyncEngine, org_a, org_b
):
    got: dict[str, list[int]] = {"a": [], "b": []}
    for key, org in [("a", org_a), ("a", org_a), ("b", org_b), ("a", org_a), ("b", org_b)]:
        async with tenant_session(app_engine, org) as s:
            got[key].append(await outbox.emit(s, org, "e", {}))
    assert got == {"a": [1, 2, 3], "b": [1, 2]}
    # A rolled-back event must not burn a number.
    with pytest.raises(RuntimeError):
        async with tenant_session(app_engine, org_a) as s:
            await outbox.emit(s, org_a, "e", {})
            raise RuntimeError
    async with tenant_session(app_engine, org_a) as s:
        assert await outbox.emit(s, org_a, "e", {}) == 4

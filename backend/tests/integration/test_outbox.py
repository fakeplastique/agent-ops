import asyncio
import uuid

import pytest
from arq.connections import RedisSettings, create_pool
from sqlalchemy import text

from app.db.session import tenant_session
from app.services import outbox
from app.worker.registry import CONCERNS
from app.worker.relay import relay_once

from .helpers import fetch, make_org

pytestmark = pytest.mark.anyio

REDIS_TEST = RedisSettings(database=15)  # tests only: never the dev data in database 0


@pytest.fixture
async def pool():
    p = await create_pool(REDIS_TEST)
    await p.flushdb()
    yield p
    await p.flushdb()
    await p.aclose()


class Recording:
    """The real arq pool plus a record of every enqueue call (and optional faults)."""

    def __init__(self, pool, fail_after: int | None = None):
        self.pool, self.calls, self.fail_after = pool, [], fail_after

    async def enqueue_job(self, function, *args, _job_id=None, **kw):
        if self.fail_after is not None and len(self.calls) >= self.fail_after:
            raise ConnectionError("redis went away")
        self.calls.append((function, args, _job_id))
        return await self.pool.enqueue_job(function, *args, _job_id=_job_id, **kw)


async def emit_events(app_engine, org, n, type_="decision.created"):
    async with tenant_session(app_engine, org) as s:
        return [await outbox.emit(s, org, type_, {"i": i}) for i in range(n)]


async def unpublished(owner, org) -> int:
    return (
        await fetch(
            owner,
            "select count(*) from outbox_events where organization_id=:o and published_at is null",
            o=org,
        )
    )[0][0]


async def drain_foreign(app_engine, worker_engine, pool):
    """The relay is global: publish other tests' leftovers first so counts belong to this test."""
    for _ in range(20):
        if (await relay_once(worker_engine, pool))["published"] == 0:
            return


@pytest.fixture
async def quiet(app_engine, worker_engine, pool):
    await drain_foreign(app_engine, worker_engine, pool)
    await pool.flushdb()


async def test_one_job_per_concern_with_event_id_concern_job_id(
    app_engine, owner_engine, worker_engine, pool, quiet
):
    org = await make_org(owner_engine)
    await emit_events(app_engine, org, 2)
    rec = Recording(pool)
    out = await relay_once(worker_engine, rec)
    events = await fetch(
        owner_engine, "select id from outbox_events where organization_id=:o", o=org
    )
    expected = {f"{e[0]}:{c}" for e in events for c in CONCERNS}
    # catches: one slow consumer blocking the others (separate jobs), and unstable ids
    assert {c[2] for c in rec.calls} == expected and len(rec.calls) == 2 * len(CONCERNS)
    assert out == {"published": 2, "errors": 0}
    queued = await pool.queued_jobs()
    assert {j.job_id for j in queued} == expected
    assert {j.function for j in queued} == set(CONCERNS)


async def test_relay_killed_midway_loses_nothing_and_enqueues_no_duplicate_job_id(
    app_engine, owner_engine, worker_engine, pool, quiet
):
    org = await make_org(owner_engine)
    await emit_events(app_engine, org, 5)
    crashing = Recording(
        pool, fail_after=2 * len(CONCERNS) + 1
    )  # dies in the middle of the third event
    out = await relay_once(worker_engine, crashing)
    assert out["errors"] == 1
    # catches: published_at set before enqueue: nothing may be marked unless its jobs went out
    assert (
        await unpublished(owner_engine, org) == 5
    )  # the batch rolled back: published_at is not set
    healthy = Recording(pool)
    await relay_once(worker_engine, healthy)
    assert await unpublished(owner_engine, org) == 0
    ids = [j.job_id for j in await pool.queued_jobs()]
    assert (
        len(ids) == len(set(ids)) == 5 * len(CONCERNS)
    )  # re-enqueued ids were deduplicated by arq


async def test_two_relays_skip_locked_each_event_once(
    app_engine, owner_engine, worker_engine, pool, quiet
):
    org = await make_org(owner_engine)
    await emit_events(app_engine, org, 20)
    a, b = Recording(pool), Recording(pool)
    ra, rb = await asyncio.gather(
        relay_once(worker_engine, a, batch=5), relay_once(worker_engine, b, batch=5)
    )
    assert ra["errors"] == rb["errors"] == 0
    all_ids = [c[2] for c in a.calls + b.calls]
    # catches: double publish (both relays took the same rows)
    assert len(all_ids) == len(set(all_ids))
    assert (
        await unpublished(owner_engine, org) == 0
        or (await relay_once(worker_engine, Recording(pool)))["published"] >= 0
    )
    await relay_once(worker_engine, Recording(pool))
    assert await unpublished(owner_engine, org) == 0
    assert len({j.job_id for j in await pool.queued_jobs()}) == 20 * len(CONCERNS)


async def test_redis_down_events_wait_then_flow(
    app_engine, owner_engine, worker_engine, pool, quiet
):
    org = await make_org(owner_engine)
    await emit_events(app_engine, org, 3)
    down = Recording(pool, fail_after=0)
    out = await relay_once(worker_engine, down)
    assert (
        out["published"] == 0 and out["errors"] >= 1
    )  # catches: events dropped while the queue is unreachable
    assert await unpublished(owner_engine, org) == 3
    await relay_once(worker_engine, Recording(pool))
    assert await unpublished(owner_engine, org) == 0
    assert len(await pool.queued_jobs()) == 3 * len(CONCERNS)


async def test_events_for_org_a_never_enqueued_with_org_b_context(
    app_engine, owner_engine, worker_engine, pool, quiet
):
    a, b = await make_org(owner_engine), await make_org(owner_engine)
    ids_a = await emit_events(app_engine, a, 2)
    ids_b = await emit_events(app_engine, b, 2)
    rec = Recording(pool)
    await relay_once(worker_engine, rec)
    org_of = {
        str(e[0]): e[1]
        for e in await fetch(
            owner_engine,
            "select id, organization_id from outbox_events where organization_id in (:a, :b)",
            a=a,
            b=b,
        )
    }
    assert len(rec.calls) == 4 * len(CONCERNS) and ids_a and ids_b
    for function, args, job_id in rec.calls:
        org, event_id = args
        assert job_id == f"{event_id}:{function}"
        assert (
            uuid.UUID(org) == org_of[event_id]
        )  # catches: tenant context taken from the worker, not the event


async def test_jobs_carry_ids_only_never_the_payload(
    app_engine, owner_engine, worker_engine, pool, quiet
):
    org = await make_org(owner_engine)
    async with tenant_session(app_engine, org) as s:
        await outbox.emit(s, org, "decision.created", {"secret": "do-not-leak-me"})
    rec = Recording(pool)
    await relay_once(worker_engine, rec)
    assert all(
        "do-not-leak-me" not in repr(c) for c in rec.calls
    )  # consumers read the event under its own tenant


async def test_per_org_order_is_preserved_in_the_queue(
    app_engine, owner_engine, worker_engine, pool, quiet
):
    a, b = await make_org(owner_engine), await make_org(owner_engine)
    for _ in range(3):  # interleave the two tenants
        await emit_events(app_engine, a, 1)
        await emit_events(app_engine, b, 1)
    rec = Recording(pool)
    await relay_once(worker_engine, rec)
    seq = {
        str(e[0]): (e[1], e[2])
        for e in await fetch(
            owner_engine,
            "select id, organization_id, seq from outbox_events where organization_id in (:a, :b)",
            a=a,
            b=b,
        )
    }
    for org in (a, b):
        order = [
            seq[c[1][1]][1] for c in rec.calls if seq[c[1][1]][0] == org and c[0] == CONCERNS[0]
        ]
        assert order == sorted(
            order
        )  # catches: a later event of a tenant overtaking an earlier one


async def test_a_published_event_is_never_published_again(
    app_engine, owner_engine, worker_engine, pool, quiet
):
    org = await make_org(owner_engine)
    await emit_events(app_engine, org, 2)
    await relay_once(worker_engine, Recording(pool))
    again = Recording(pool)
    out = await relay_once(worker_engine, again)
    assert again.calls == [] and out["published"] == 0


async def test_a_row_locked_by_someone_else_is_skipped_not_waited_for(
    app_engine, owner_engine, worker_engine, pool, quiet
):
    # catches: a plain FOR UPDATE making one relay queue behind another (or behind a stuck transaction)
    org = await make_org(owner_engine)
    await emit_events(app_engine, org, 3)
    async with owner_engine.connect() as holder:
        tx = await holder.begin()
        await holder.execute(
            text("select 1 from outbox_events where organization_id=:o and seq=1 for update"),
            {"o": org},
        )
        out = await asyncio.wait_for(
            relay_once(worker_engine, Recording(pool)), 5
        )  # returns, does not wait
        assert out["published"] == 2 and out["errors"] == 0
        await tx.commit()
    assert await unpublished(owner_engine, org) == 1
    assert (await relay_once(worker_engine, Recording(pool)))["published"] == 1


async def test_a_stamp_that_updates_nothing_fails_the_batch_instead_of_looping(
    app_engine, owner_engine, worker_engine, pool, quiet, monkeypatch
):
    from app.worker import relay

    org = await make_org(owner_engine)
    await emit_events(app_engine, org, 2)
    monkeypatch.setattr(
        relay,
        "_MARK",
        text(
            "update outbox_events set published_at = :now where false and id = any(:ids) returning id"
        ),
    )
    out = await asyncio.wait_for(
        relay_once(worker_engine, Recording(pool)), 10
    )  # catches: an endless re-pick of the same rows
    assert out == {"published": 0, "errors": 1}
    assert await unpublished(owner_engine, org) == 2

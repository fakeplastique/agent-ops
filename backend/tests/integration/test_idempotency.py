import asyncio
import uuid
from datetime import timedelta

import pytest

from app.api.idempotency import begin, finish
from app.db.session import tenant_session
from app.domain.errors import IdempotencyKeyReuse, InvalidInput
from app.services.usage import consume

from .helpers import FakeClock, make_org, utc

pytestmark = pytest.mark.anyio


async def create(engine, org, pid, key, body, clock, route="POST /decision-requests"):
    """A request handler in miniature: claim the key, apply one counted effect, store the answer."""
    async with tenant_session(engine, org) as s:
        replay = await begin(s, org, pid, key, route, body, clock)
        if replay:
            return replay.status, replay.body
        n = await consume(s, org, "idem_effect", clock)
        out = {"effect": n}
        await finish(s, org, pid, key, route, 201, out)
        return 201, out


async def effects(owner, org) -> int:
    from .helpers import fetch

    rows = await fetch(
        owner,
        "select value from usage_counters where organization_id=:o and metric='idem_effect'",
        o=org,
    )
    return rows[0][0] if rows else 0


async def test_concurrent_same_key_one_effect_one_response(app_engine, owner_engine):
    org, pid, clock = await make_org(owner_engine), uuid.uuid4(), FakeClock(utc(2026, 1, 1))
    # catches: check-then-insert on the key
    out = await asyncio.gather(
        *(create(app_engine, org, pid, "k1", {"a": 1}, clock) for _ in range(8))
    )
    assert set(map(str, out)) == {str((201, {"effect": 1}))}
    assert await effects(owner_engine, org) == 1


async def test_same_key_different_body_422_idempotency_key_reuse(app_engine, owner_engine):
    org, pid, clock = await make_org(owner_engine), uuid.uuid4(), FakeClock(utc(2026, 1, 1))
    await create(app_engine, org, pid, "k1", {"a": 1}, clock)
    # catches: replaying a response for a different request
    with pytest.raises(IdempotencyKeyReuse) as e:
        await create(app_engine, org, pid, "k1", {"a": 2}, clock)
    assert e.value.code == "idempotency_key_reuse"
    assert await effects(owner_engine, org) == 1
    # key order in the body is not a different request
    assert await create(app_engine, org, pid, "k1", {"a": 1}, clock) == (201, {"effect": 1})


async def test_same_key_other_principal_or_route_is_independent(app_engine, owner_engine):
    org, other = await make_org(owner_engine), await make_org(owner_engine)
    p1, p2, clock = uuid.uuid4(), uuid.uuid4(), FakeClock(utc(2026, 1, 1))
    # catches: keys shared across tenants
    await create(app_engine, org, p1, "k", {}, clock)
    await create(app_engine, org, p2, "k", {}, clock)
    await create(app_engine, org, p1, "k", {}, clock, route="POST /approval-steps/x/decide")
    await create(app_engine, other, p1, "k", {}, clock)
    assert await effects(owner_engine, org) == 3
    assert await effects(owner_engine, other) == 1


async def test_key_expires_after_24h_with_fake_clock(app_engine, owner_engine):
    org, pid = await make_org(owner_engine), uuid.uuid4()
    t0 = utc(2026, 1, 1)
    await create(app_engine, org, pid, "k", {"a": 1}, FakeClock(t0))
    # catches: keys living forever
    near = FakeClock(t0 + timedelta(hours=24) - timedelta(seconds=1))
    assert await create(app_engine, org, pid, "k", {"a": 1}, near) == (201, {"effect": 1})
    late = FakeClock(t0 + timedelta(hours=24, seconds=1))
    assert await create(app_engine, org, pid, "k", {"a": 2}, late) == (201, {"effect": 2})


async def test_a_failed_request_leaves_no_key_behind(app_engine, owner_engine):
    org, pid, clock = await make_org(owner_engine), uuid.uuid4(), FakeClock(utc(2026, 1, 1))
    # catches: a rolled-back effect still reserving its key, so the retry replays nothing
    with pytest.raises(RuntimeError):
        async with tenant_session(app_engine, org) as s:
            await begin(s, org, pid, "k", "r", {}, clock)
            raise RuntimeError
    assert await create(app_engine, org, pid, "k", {}, clock, route="r") == (201, {"effect": 1})


async def test_key_shape_is_validated(app_engine, owner_engine):
    org, clock = await make_org(owner_engine), FakeClock(utc(2026, 1, 1))
    for bad in ("", "x" * 256, "bad\x00key", "new\nline"):
        async with tenant_session(app_engine, org) as s:
            with pytest.raises(InvalidInput):
                await begin(s, org, uuid.uuid4(), bad, "r", {}, clock)

"""FM22 / I17: a hanging or hostile receiver cannot stall the webhook worker."""

import asyncio
from datetime import timedelta

import pytest

from app.config import settings
from app.worker import webhooks as worker
from tests.integration.helpers import NOW, fetch, make_world
from tests.integration.webhook_harness import Receiver, emit, sender, subscribe

pytestmark = pytest.mark.anyio


@pytest.fixture(autouse=True)
def _http(monkeypatch):
    monkeypatch.setattr(settings, "webhooks_allow_http", True)  # local receivers speak plain http


async def _rows(owner, event):
    return {
        sub: (status, attempt, error, nxt)
        for sub, status, attempt, error, nxt in await fetch(
            owner,
            "select subscription_id, status, attempt, last_error, next_attempt_at"
            " from webhook_deliveries where event_id = :e",
            e=event,
        )
    }


async def test_hanging_endpoint_times_out_at_5s_and_other_deliveries_proceed(
    app_engine, owner_engine
):
    # catches: one slow receiver stalling the worker (FM22: never answers, or one byte a second)
    w = await make_world(owner_engine)
    hang, trickle, fast = Receiver("hang"), Receiver("trickle"), Receiver()
    receivers = {8081: hang, 8082: trickle, 8083: fast}
    servers = [await r.start() for r in receivers.values()]
    subs = {p: await subscribe(app_engine, w.org, p) for p in receivers}
    event = await emit(app_engine, w.org)
    loop = asyncio.get_running_loop()
    start = loop.time()
    # The default 5 s deadline: both slow ones are cut at 5 s, side by side, not one after the other.
    await worker.fan_out(app_engine, w.org, event, NOW, send=sender(receivers))
    took = loop.time() - start
    for s in servers:
        s.close()
    assert 4.5 < took < 7.5, took
    assert fast.seen and fast.seen[0].at - start < 1.5  # the fast one was not queued behind them
    rows = await _rows(owner_engine, event)
    assert rows[subs[8083]["id"]][:3] == ("delivered", 1, None)
    for p in (8081, 8082):
        status, attempt, error, nxt = rows[subs[p]["id"]]
        assert (status, attempt, error) == ("pending", 1, "timeout")
        assert nxt == NOW.now() + timedelta(seconds=30)  # first backoff step


async def test_huge_response_body_is_not_buffered(app_engine, owner_engine):
    # catches: memory blowup from a hostile receiver (the status is enough; the body is never read)
    w = await make_world(owner_engine)
    huge = Receiver("huge")
    server = await huge.start()
    sub = await subscribe(app_engine, w.org, 8084)
    event = await emit(app_engine, w.org)
    loop = asyncio.get_running_loop()
    start = loop.time()
    await worker.fan_out(app_engine, w.org, event, NOW, send=sender({8084: huge}))
    took = loop.time() - start
    server.close()
    assert took < 2, took
    assert huge.sent < 50 * 2**20, huge.sent  # the client hung up long before 10 GB
    assert (await _rows(owner_engine, event))[sub["id"]][:3] == ("delivered", 1, None)

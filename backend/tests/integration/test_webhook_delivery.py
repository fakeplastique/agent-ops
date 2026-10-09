"""I17 / SC23: webhook delivery is signed, at least once with a stable webhook-id, retried 5 times, tenant-scoped."""

import asyncio
import json
import uuid
from contextlib import asynccontextmanager
from datetime import timedelta

import pytest
from arq.worker import Retry
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import create_async_engine

from app.config import settings
from app.db.session import tenant_session
from app.domain.errors import SubscriptionPaused
from app.services import webhook_security as ws
from app.services import webhooks
from app.worker import webhooks as worker
from tests.integration.helpers import (
    NOW,
    FakeClock,
    _exec,
    cast,
    fetch,
    make_world,
    open_request,
    voters,
)
from tests.integration.webhook_harness import Receiver, emit, sender, subscribe

pytestmark = pytest.mark.anyio


@pytest.fixture(autouse=True)
async def _http(monkeypatch, owner_engine):
    monkeypatch.setattr(settings, "webhooks_allow_http", True)  # local receivers speak plain http
    # The retry sweep is global: retire other tests' pending rows so each test owns what the sweep sees.
    async with owner_engine.begin() as c:
        await c.exec_driver_sql(
            "update webhook_deliveries set status = 'dead', next_attempt_at = null"
            " where status = 'pending'"
        )


async def _row(owner, delivery):
    [row] = await fetch(
        owner,
        "select status, attempt, next_attempt_at, last_status_code, last_error"
        " from webhook_deliveries where id = :d",
        d=delivery,
    )
    return tuple(row)


async def _only_delivery(owner, event) -> uuid.UUID:
    [(d,)] = await fetch(owner, "select id from webhook_deliveries where event_id = :e", e=event)
    return d


async def test_5_attempts_backoff_then_dead_letter_and_redeliver_reuses_webhook_id(
    app_engine, owner_engine, worker_engine
):
    # catches: new id per retry (receiver cannot de-duplicate)
    w = await make_world(owner_engine)
    rx = Receiver(statuses=(500,))
    server = await rx.start()
    send = sender({8090: rx})
    sub = await subscribe(app_engine, w.org, 8090)
    event = await emit(app_engine, w.org, payload={"id": "r1"})
    t0 = NOW.now()
    assert await worker.fan_out(app_engine, w.org, event, NOW, send=send) == 1
    delivery = await _only_delivery(owner_engine, event)
    at = t0
    for n, wait in enumerate(worker.BACKOFF, start=1):
        assert await _row(owner_engine, delivery) == (
            "pending", n, at + timedelta(seconds=wait), 500, "HTTP 500"
        )  # fmt: skip
        early = FakeClock(at + timedelta(seconds=wait - 1))
        assert await worker.retry_due(worker_engine, app_engine, early, send=send) == 0
        at += timedelta(seconds=wait)
        assert await worker.retry_due(worker_engine, app_engine, FakeClock(at), send=send) == 1
    assert await _row(owner_engine, delivery) == ("dead", 5, None, 500, "HTTP 500")
    later = FakeClock(at + timedelta(days=1))
    assert await worker.retry_due(worker_engine, app_engine, later, send=send) == 0
    assert len(rx.seen) == 5

    # Manual redeliver: the same row, so the same webhook-id, and a fresh 5 attempts.
    rx.statuses = (204,)
    admin = uuid.uuid4()
    async with tenant_session(app_engine, w.org) as s:
        again = await webhooks.redeliver(s, w.org, admin, sub["id"], delivery, later.now())
    assert again["id"] == delivery and (again["status"], again["attempt"]) == ("pending", 0)
    assert await worker.retry_due(worker_engine, app_engine, later, send=send) == 1
    server.close()
    assert (await _row(owner_engine, delivery))[:2] == ("delivered", 1)
    assert len(rx.seen) == 6
    assert {r.headers["webhook-id"] for r in rx.seen} == {str(delivery)}
    assert len({r.body for r in rx.seen}) == 1  # the same message every time
    for r in rx.seen:  # each attempt is signed anew over its own timestamp
        stamp = int(r.headers["webhook-timestamp"])
        assert ws.verify(
            sub["secret"], str(delivery), stamp, r.body, r.headers["webhook-signature"], now=stamp
        )
    sent = json.loads(rx.seen[0].body)
    assert (sent["type"], sent["seq"], sent["data"]) == ("decision.created", 1, {"id": "r1"})
    assert set(sent) == {"type", "timestamp", "seq", "data"}
    audit = await fetch(
        owner_engine,
        "select action, actor_id from audit_logs where entity_id = :d",
        d=delivery,
    )
    assert audit == [("redelivered", admin)]


async def test_a_rerun_job_sends_nothing_new(app_engine, owner_engine):
    # catches: the relay re-sending an event after arq forgot the job id (registry.py contract)
    w = await make_world(owner_engine)
    rx = Receiver()
    server = await rx.start()
    await subscribe(app_engine, w.org, 8091)
    event = await emit(app_engine, w.org)
    for _ in range(2):
        await worker.fan_out(app_engine, w.org, event, NOW, send=sender({8091: rx}))
    server.close()
    assert len(rx.seen) == 1


async def test_delivery_row_records_status_and_error_without_body(
    app_engine, owner_engine, worker_engine
):
    # catches: payload stored in delivery log
    w = await make_world(owner_engine)
    rx = Receiver(statuses=(503,), reply=b"RESPONSE-BODY-MARKER")
    server = await rx.start()
    await subscribe(app_engine, w.org, 8092)
    event = await emit(app_engine, w.org, payload={"secret_note": "PAYLOAD-MARKER"})
    await worker.fan_out(app_engine, w.org, event, NOW, send=sender({8092: rx}))
    server.close()
    delivery = await _only_delivery(owner_engine, event)
    assert (await _row(owner_engine, delivery))[3:] == (503, "HTTP 503")
    [(whole,)] = await fetch(
        owner_engine,
        "select row_to_json(d)::text from webhook_deliveries d where id = :d",
        d=delivery,
    )
    assert "MARKER" not in whole
    assert b"PAYLOAD-MARKER" in rx.seen[0].body  # it did go out, to the receiver only

    # A refusal and a dead connection are recorded by rule or kind, never by address.
    rows = {}
    for port, url in ((8093, None), (8094, "http://127.0.0.1:8094/h")):
        sub = await subscribe(app_engine, w.org, port)
        if url:  # a row edited past the API: delivery re-checks it
            await _exec(
                owner_engine, "update webhook_subscriptions set url = :u where id = :i",
                u=url, i=sub["id"],
            )  # fmt: skip
        rows[port] = sub["id"]
    event = await emit(app_engine, w.org)
    await worker.fan_out(app_engine, w.org, event, NOW, send=sender({8093: Receiver(port=1)}))
    got = dict(
        await fetch(
            owner_engine,
            "select subscription_id, last_error from webhook_deliveries where event_id = :e",
            e=event,
        )
    )
    assert got[rows[8093]].startswith("connection failed: ")
    assert got[rows[8094]] == "refused: private address range"


async def test_event_for_org_a_never_sent_to_org_b_subscription(app_engine, owner_engine):
    # catches: fan-out ignoring the tenant
    a, b = await make_world(owner_engine), await make_world(owner_engine)
    rx_a, rx_b = Receiver(), Receiver()
    servers = [await rx_a.start(), await rx_b.start()]
    send = sender({8095: rx_a, 8096: rx_b})
    await subscribe(app_engine, a.org, 8095)
    await subscribe(app_engine, b.org, 8096)
    event = await emit(app_engine, a.org)
    # A job naming B with A's event (a mixed-up or forged job) finds nothing to send.
    assert await worker.fan_out(app_engine, b.org, event, NOW, send=send) == 0
    assert await worker.fan_out(app_engine, a.org, event, NOW, send=send) == 1
    for s in servers:
        s.close()
    assert len(rx_a.seen) == 1 and rx_b.seen == []


async def test_only_active_subscriptions_wanting_the_type_get_it(app_engine, owner_engine):
    w = await make_world(owner_engine)
    rx = Receiver()
    server = await rx.start()
    wants = await subscribe(app_engine, w.org, 8097)
    await subscribe(app_engine, w.org, 8097, events=("vote.cast",))
    muted = await subscribe(app_engine, w.org, 8097)
    async with tenant_session(app_engine, w.org) as s:
        await webhooks.update_subscription(s, w.org, uuid.uuid4(), muted["id"], {"active": False})
    event = await emit(app_engine, w.org)
    await worker.fan_out(app_engine, w.org, event, NOW, send=sender({8097: rx}))
    server.close()
    subs = await fetch(
        owner_engine, "select subscription_id from webhook_deliveries where event_id = :e", e=event
    )
    assert subs == [(wants["id"],)] and len(rx.seen) == 1


async def test_a_job_that_cannot_reach_the_database_is_retried_not_dropped():
    # catches: an event lost for good because the database blinked while its job ran (the relay
    # already stamped it published, so nothing else would ever send it)
    down = create_async_engine("postgresql+asyncpg://nobody:x@127.0.0.1:1/none")
    ctx = {"work_engine": down, "clock": NOW, "job_try": 2}
    with pytest.raises(Retry):
        await worker.webhooks(ctx, str(uuid.uuid4()), str(uuid.uuid4()))
    await down.dispose()


async def test_round_one_vote_events_carry_session_and_round_only_before_reveal(
    app_engine, owner_engine
):
    # catches: blind verdicts in a webhook
    w = await make_world(owner_engine)
    rx = Receiver()
    server = await rx.start()
    await subscribe(app_engine, w.org, 8098, events=("vote.cast", "round.revealed"))
    req = await open_request(app_engine, w)
    for s in await voters(
        owner_engine, w, 3
    ):  # the third vote fills the quorum and reveals round 1
        await cast(app_engine, w, req, s, verdict="reject", confidence=0.42)
    # And events that wrongly carry a verdict or rationale: the webhook body still must not.
    planted = {"decision_request_id": str(req["id"]), "round": 1, "verdict": "reject",
               "confidence": 0.42, "rationale": "because"}  # fmt: skip
    await emit(app_engine, w.org, "vote.cast", planted)
    await emit(app_engine, w.org, "vote.cast", planted | {"round": 2})
    events = await fetch(
        owner_engine,
        "select id from outbox_events where organization_id = :o order by seq",
        o=w.org,
    )
    for (event,) in events:
        await worker.fan_out(app_engine, w.org, event, NOW, send=sender({8098: rx}))
    server.close()
    sent = [json.loads(r.body) for r in rx.seen]
    assert [m["type"] for m in sent] == ["vote.cast"] * 3 + ["round.revealed"] + ["vote.cast"] * 2
    for m in sent[:3] + sent[4:5]:
        assert m["data"]["round"] == 1
        assert not {"verdict", "confidence", "rationale"} & set(m["data"]), m
    assert {v["verdict"] for v in sent[3]["data"]["votes"]} == {"reject"}  # revealed: now it may
    assert (sent[5]["data"]["verdict"], "rationale" in sent[5]["data"]) == ("reject", False)


async def _refused(engine, sql, **params) -> bool:
    try:
        async with engine.begin() as c:
            await c.execute(text(sql), params)
    except DBAPIError:
        return True
    return False


async def test_the_sweep_sees_ids_only_and_a_row_keeps_its_identity(
    app_engine, owner_engine, worker_engine
):
    # catches: the cross-tenant worker role reading URLs or errors, or a tenant moving a delivery
    w = await make_world(owner_engine)
    sub = await subscribe(app_engine, w.org, 8099)
    event = await emit(app_engine, w.org)
    await worker.fan_out(app_engine, w.org, event, NOW, send=sender({8099: Receiver(port=1)}))
    delivery = await _only_delivery(owner_engine, event)
    for sql in (
        "select last_error from webhook_deliveries",
        "select event_id from webhook_deliveries",
        "select url from webhook_subscriptions",
        "update webhook_deliveries set status = 'dead'",
    ):
        assert await _refused(worker_engine, sql), sql
    async with worker_engine.connect() as c:
        seen = (await c.execute(text("select id from webhook_deliveries"))).scalars().all()
    assert delivery in seen  # pending rows of every tenant, by id
    other = await subscribe(app_engine, w.org, 8099)
    for col, value in (("subscription_id", other["id"]), ("event_id", uuid.uuid4()),
                       ("organization_id", uuid.uuid4()), ("id", uuid.uuid4())):  # fmt: skip
        sql = f"update webhook_deliveries set {col} = :v where id = :d"
        try:
            async with tenant_session(app_engine, w.org) as s:
                await s.execute(text(sql), {"v": value, "d": delivery})
            moved = True
        except DBAPIError:
            moved = False
        assert not moved, col
    assert sub["id"] == (await fetch(
        owner_engine, "select subscription_id from webhook_deliveries where id = :d", d=delivery
    ))[0][0]  # fmt: skip


async def _queued(owner, org, sub, event, at) -> uuid.UUID:
    """A due delivery row planted directly (as if a worker died before its first send)."""
    sql = text(
        "insert into webhook_deliveries (organization_id, subscription_id, event_id, event_type,"
        " next_attempt_at) values (:o, :s, :e, 'decision.created', :at) returning id"
    )
    async with owner.begin() as c:
        return (await c.execute(sql, {"o": org, "s": sub, "e": event, "at": at})).scalar_one()


async def test_a_secret_that_does_not_open_cannot_starve_the_sweep(
    app_engine, owner_engine, worker_engine
):
    # catches: a claim that raises rolling back, so the same poison rows head every batch forever
    w = await make_world(owner_engine)
    rx = Receiver()
    server = await rx.start()
    bad, good = await subscribe(app_engine, w.org, 8100), await subscribe(app_engine, w.org, 8100)
    await _exec(  # another row's ciphertext: its AAD names another subscription, so it never opens
        owner_engine,
        "update webhook_subscriptions set secret_ciphertext ="
        " (select secret_ciphertext from webhook_subscriptions where id = :g) where id = :b",
        g=good["id"], b=bad["id"],
    )  # fmt: skip
    t0 = NOW.now()
    poison = [
        await _queued(owner_engine, w.org, bad["id"], await emit(app_engine, w.org), t0 - timedelta(minutes=10))
        for _ in range(4)
    ]  # fmt: skip
    healthy = await _queued(
        owner_engine, w.org, good["id"], await emit(app_engine, w.org), t0 - timedelta(minutes=1)
    )
    send = sender({8100: rx})
    for _ in range(2):  # batch 3 < 4 poison rows: only moving on lets the healthy one through
        await worker.retry_due(worker_engine, app_engine, NOW, send=send, batch=3)
    server.close()
    assert (await _row(owner_engine, healthy))[:2] == ("delivered", 1)
    for d in poison:
        assert await _row(owner_engine, d) == (
            "pending", 1, t0 + timedelta(seconds=30), None, "secret unavailable: SealError"
        )  # fmt: skip
    assert len(rx.seen) == 1  # nothing unsigned ever went out


async def test_pausing_dead_letters_with_its_reason_and_redeliver_waits_for_resume(
    app_engine, owner_engine, worker_engine
):
    # catches: a paused hook's retries dying as "inactive", and a redeliver that 202s then dies silently
    w = await make_world(owner_engine)
    rx = Receiver(statuses=(500,))
    server = await rx.start()
    send = sender({8101: rx})
    sub = await subscribe(app_engine, w.org, 8101)
    await worker.fan_out(app_engine, w.org, await emit(app_engine, w.org), NOW, send=send)
    [(delivery,)] = await fetch(
        owner_engine, "select id from webhook_deliveries where subscription_id = :s", s=sub["id"]
    )
    admin = uuid.uuid4()

    async def set_active(on: bool) -> None:
        async with tenant_session(app_engine, w.org) as s:
            await webhooks.update_subscription(s, w.org, admin, sub["id"], {"active": on})

    async def redeliver() -> dict:
        async with tenant_session(app_engine, w.org) as s:
            return await webhooks.redeliver(s, w.org, admin, sub["id"], delivery, due.now())

    await set_active(False)
    due = FakeClock(NOW.now() + timedelta(seconds=30))
    assert await worker.retry_due(worker_engine, app_engine, due, send=send) == 1
    paused = ("dead", 1, None, 500, "subscription is paused")
    assert await _row(owner_engine, delivery) == paused and len(rx.seen) == 1  # not sent again
    with pytest.raises(SubscriptionPaused):
        await redeliver()
    assert await _row(owner_engine, delivery) == paused  # the reason is kept, not wiped
    await set_active(True)
    rx.statuses = (204,)
    assert (await redeliver())["status"] == "pending"
    assert await worker.retry_due(worker_engine, app_engine, due, send=send) == 1
    server.close()
    assert (await _row(owner_engine, delivery))[:2] == ("delivered", 1)


async def test_one_batch_never_holds_more_sessions_than_the_pool(
    app_engine, owner_engine, monkeypatch
):
    # catches: 50 sends at once queueing on a 5+10 pool until a finish times out and is lost
    w = await make_world(owner_engine)
    for _ in range(12):
        await subscribe(app_engine, w.org, 8102)
    real, open_now, peak = worker.tenant_session, [0], [0]

    @asynccontextmanager
    async def counted(engine, org):
        open_now[0] += 1
        peak[0] = max(peak[0], open_now[0])
        try:
            async with real(engine, org) as s:
                await asyncio.sleep(0.05)  # a session that takes a moment, as under load
                yield s
        finally:
            open_now[0] -= 1

    async def slow_ok(url, body, headers) -> int:
        await asyncio.sleep(0.2)
        return 204

    monkeypatch.setattr(worker, "tenant_session", counted)
    assert (
        await worker.fan_out(app_engine, w.org, await emit(app_engine, w.org), NOW, send=slow_ok)
        == 12
    )
    assert 1 < peak[0] <= worker.DB_SLOTS <= 5, peak  # side by side, but within the pool's 5
    done = await fetch(
        owner_engine,
        "select count(*) from webhook_deliveries d join webhook_subscriptions s"
        " on s.id = d.subscription_id where s.organization_id = :o and d.status = 'delivered'",
        o=w.org,
    )
    assert done == [(12,)]

import asyncio
import uuid
from functools import partial

import pytest
from sqlalchemy import text

from app.adapters import pubsub_redis
from app.api import deps
from app.db.session import tenant_session
from app.main import app
from app.realtime import longpoll, ws
from app.services import outbox
from app.worker.relay import relay_once
from tests.api.harness import agent_auth, user_auth

from .helpers import fetch, make_member, make_org, make_world, staff
from .ws_harness import TICKET, WS, ticket

pytestmark = pytest.mark.anyio


async def plant(app_engine, org, to: str = "pro") -> int:
    async with tenant_session(app_engine, org) as s:
        return await outbox.emit(
            s, org, "plan.changed", {"organization_id": str(org), "from": "free", "to": to}
        )


async def _cookie_user_in(web, owner_engine, *orgs) -> uuid.UUID:
    email = f"{uuid.uuid4()}@x.test"
    await web.login(email)
    user = (await fetch(owner_engine, "select id from users where email=:e", e=email))[0][0]
    for org in orgs:
        await make_member(owner_engine, org, user, "member")
    await web.post("/api/v1/auth/organization", json={"organization_id": str(orgs[0])})
    return user


async def test_ticket_single_use_expires_30s_bound_to_user_and_org(
    web, app_engine, owner_engine, monkeypatch
):
    one, two = await make_org(owner_engine), await make_org(owner_engine)
    s1, s2 = await plant(app_engine, one, "one"), await plant(app_engine, two, "two")
    await _cookie_user_in(web, owner_engine, one, two)
    t = await ticket(web)
    keys = [k async for k in web_redis(web).scan_iter("rt:ticket:*")]
    assert len(keys) == 1 and t.encode() not in keys[0]  # stored by hash, never the ticket itself
    assert 0 < await web_redis(web).ttl(keys[0]) <= 30
    # catches: the session cookie itself parked in Redis (the ticket names it by hash)
    assert web.client.cookies[deps.COOKIE].encode() not in await web_redis(web).get(keys[0])
    # A query parameter cannot name another org.
    sock = WS(ticket=t, organization_id=str(two), org=str(two))
    assert (await sock.open())["type"] == "websocket.accept"
    frame = await sock.frame()
    # catches: a ticket for another org (or org taken from input)
    assert frame["organization_id"] == str(one) and [e["seq"] for e in frame["events"]] == [s1]
    assert "two" not in str(frame) and s2 == 1
    await sock.leave()
    # catches: a replayable ticket (refused before the upgrade)
    assert (await WS(ticket=t).open())["type"] == "websocket.close"
    monkeypatch.setattr(ws, "TICKET_TTL", 1)
    late = await ticket(web)
    await asyncio.sleep(1.2)
    assert (await WS(ticket=late).open())["type"] == "websocket.close"  # catches: no expiry
    for junk in ("", "x", t + "x"):
        assert (await WS(ticket=junk).open())["type"] == "websocket.close"


def web_redis(_web):
    return app.dependency_overrides[deps.get_redis]()  # the web fixture's Redis


async def test_ticket_is_for_users_whose_membership_still_holds(web, app_engine, owner_engine):
    w = await make_world(owner_engine)
    res = await web.client.post(TICKET, headers=await agent_auth(app_engine, w.org, w.identity))
    assert res.status_code == 403  # the feed is for users, as GET /events
    org = await make_org(owner_engine)
    await staff(owner_engine, org, "org_admin")
    user = await staff(owner_engine, org, "member")
    t = await ticket(web, await user_auth(app_engine, org, user))
    async with owner_engine.begin() as c:
        await c.execute(text("delete from memberships where user_id=:u"), {"u": user})
    # catches: a ticket outliving the membership it was minted under
    assert (await WS(ticket=t).open())["type"] == "websocket.close"


async def _pat_member(app_engine, owner_engine) -> tuple[uuid.UUID, uuid.UUID, dict[str, str]]:
    org = await make_org(owner_engine)
    await staff(owner_engine, org, "org_admin")  # someone stays when the member leaves
    user = await staff(owner_engine, org, "member")
    return org, user, await user_auth(app_engine, org, user)


async def _seqs(sock: WS, n: int) -> list[int]:
    got: list[int] = []
    while len(got) < n:
        got += [e["seq"] for e in (await sock.frame(3))["events"]]
    return got


async def _quiet(sock: WS, wait: float = 0.5) -> None:
    await asyncio.sleep(wait)
    assert sock.from_app.empty(), sock.from_app.get_nowait()


async def _subscribers(redis) -> list[dict]:
    db = redis.connection_pool.connection_kwargs.get("db", 0)
    return [c for c in await redis.client_list(_type="pubsub") if int(c["db"]) == db]


class _NoQueue:
    async def enqueue_job(self, *args, **kw) -> None:
        return None


async def test_replay_then_live_without_gap_or_duplicate_at_the_seam(
    web, app_engine, owner_engine, worker_engine, monkeypatch
):
    monkeypatch.setattr(ws, "BATCH", 2)  # the replay spans pages
    monkeypatch.setattr(ws, "FALLBACK", 30)  # only a wake-up delivers within the test's patience
    org, _user, h = await _pat_member(app_engine, owner_engine)
    for i in range(5):
        await plant(app_engine, org, str(i))
    redis, real, seamed = web_redis(web), ws.read_after, asyncio.Event()

    async def seam(engine, o, after, limit):
        rows = await real(engine, o, after, limit)
        if not rows and not seamed.is_set():  # the replay just ran dry: commit and wake right here
            seamed.set()
            await plant(app_engine, org, "seam")
            await pubsub_redis.publish(redis, [org])
        return rows

    monkeypatch.setattr(ws, "read_after", seam)
    sock = WS(ticket=await ticket(web, h), after="1")
    assert (await sock.open())["type"] == "websocket.accept"
    # catches: subscribing after the replay (the seam's event waits for the next poll) or re-sending one
    assert await _seqs(sock, 5) == [2, 3, 4, 5, 6]
    await _quiet(sock)  # the server drained its wake-ups and sleeps on FALLBACK
    # Live: the relay publishes the wake-up once the event committed.
    seq = await plant(app_engine, org, "live")
    await relay_once(worker_engine, _NoQueue(), notify=partial(pubsub_redis.publish, redis))
    assert await _seqs(sock, 1) == [seq]
    await _quiet(sock)
    await sock.leave()


async def test_redis_pubsub_loss_falls_back_to_db_poll_without_losing_events(
    web, app_engine, owner_engine, monkeypatch
):
    monkeypatch.setattr(ws, "FALLBACK", 0.3)
    org, _user, h = await _pat_member(app_engine, owner_engine)
    redis = web_redis(web)
    sock = WS(ticket=await ticket(web, h))
    assert (await sock.open())["type"] == "websocket.accept"
    await asyncio.sleep(0.2)
    subs = await _subscribers(redis)
    assert subs, "the socket holds no subscription"
    for c in subs:  # this test's Redis database only: other lanes share the server
        await redis.client_kill_filter(_id=c["id"])
    seqs = [await plant(app_engine, org, str(i)) for i in range(3)]
    await pubsub_redis.publish(redis, [org])  # into the void
    # catches: pub/sub treated as the source of truth (events committed while it was down never arrive)
    assert await _seqs(sock, 3) == seqs
    more = await plant(app_engine, org, "after")
    assert await _seqs(sock, 1) == [more]
    await sock.leave()


async def test_slow_client_is_dropped_not_buffered_unbounded(
    web, app_engine, owner_engine, monkeypatch
):
    monkeypatch.setattr(ws, "SEND_TIMEOUT", 0.3)
    org, user, h = await _pat_member(app_engine, owner_engine)
    for i in range(3):
        await plant(app_engine, org, str(i))
    reads, real = [], ws.read_after

    async def counted(*args):
        reads.append(args[2])
        return await real(*args)

    monkeypatch.setattr(ws, "read_after", counted)
    sock = WS(stall=True, ticket=await ticket(web, h))
    assert (await sock.open())["type"] == "websocket.accept"
    assert sock.task is not None
    # catches: a stalled socket holding its slot, subscription and a growing backlog forever
    await asyncio.wait_for(sock.task, 3)
    for i in range(3):
        await plant(app_engine, org, f"later-{i}")
    await asyncio.sleep(0.3)
    assert reads == [0]  # one batch read, never further ahead of what the client took
    redis = web_redis(web)
    assert await redis.zcard(longpoll.waiter_key(org, user)) == 0
    assert await _subscribers(redis) == []


async def test_access_lapse_mid_stream_closes_without_delivering(
    web, app_engine, owner_engine, monkeypatch
):
    monkeypatch.setattr(ws, "FALLBACK", 0.3)
    org, user, h = await _pat_member(app_engine, owner_engine)
    sock = WS(ticket=await ticket(web, h))
    assert (await sock.open())["type"] == "websocket.accept"
    first = await plant(app_engine, org, "before")
    assert await _seqs(sock, 1) == [first]
    async with owner_engine.begin() as c:
        await c.execute(text("delete from memberships where user_id=:u"), {"u": user})
    # catches: a socket outliving the membership (I19), even with nothing to send (no slot held idle)
    assert (await sock.closed(3))["code"] == 1008
    assert sock.task is not None
    await asyncio.wait_for(sock.task, 3)


async def _closes_without_x_after(sock: WS, app_engine, org) -> None:
    await plant(app_engine, org, "x-AFTER")
    msg = await asyncio.wait_for(sock.from_app.get(), 3)
    assert msg["type"] == "websocket.close" and msg["code"] == 1008, msg
    assert sock.task is not None
    await asyncio.wait_for(sock.task, 3)


async def test_revoked_pat_mid_stream_closes_without_delivering(
    web, app_engine, owner_engine, monkeypatch
):
    monkeypatch.setattr(ws, "FALLBACK", 0.3)
    org, user, h = await _pat_member(app_engine, owner_engine)
    sock = WS(ticket=await ticket(web, h))
    assert (await sock.open())["type"] == "websocket.accept"
    async with owner_engine.begin() as c:
        await c.execute(
            text("update personal_access_tokens set revoked_at = now() where user_id=:u"),
            {"u": user},
        )
    # catches: a revoked token streaming on until the socket's lifetime ends (I13)
    await _closes_without_x_after(sock, app_engine, org)


async def test_logged_out_session_mid_stream_closes_without_delivering(
    web, app_engine, owner_engine, monkeypatch
):
    monkeypatch.setattr(ws, "FALLBACK", 0.3)
    org = await make_org(owner_engine)
    await _cookie_user_in(web, owner_engine, org)
    sock = WS(ticket=await ticket(web))
    assert (await sock.open())["type"] == "websocket.accept"
    assert (await web.post("/api/v1/auth/logout")).status_code == 204
    # catches: a socket outliving the session that minted its ticket
    await _closes_without_x_after(sock, app_engine, org)


async def test_session_switched_to_another_org_mid_stream_closes(
    web, app_engine, owner_engine, monkeypatch
):
    monkeypatch.setattr(ws, "FALLBACK", 0.3)
    one, two = await make_org(owner_engine), await make_org(owner_engine)
    await _cookie_user_in(web, owner_engine, one, two)
    sock = WS(ticket=await ticket(web))
    assert (await sock.open())["type"] == "websocket.accept"
    await web.post("/api/v1/auth/organization", json={"organization_id": str(two)})
    # catches: a socket streaming the organization the user just left the view of (as the long-poll)
    await _closes_without_x_after(sock, app_engine, one)


async def test_ticket_minted_then_credential_revoked_is_refused(web, app_engine, owner_engine):
    _org, user, h = await _pat_member(app_engine, owner_engine)
    t = await ticket(web, h)
    async with owner_engine.begin() as c:
        await c.execute(
            text("update personal_access_tokens set revoked_at = now() where user_id=:u"),
            {"u": user},
        )
    # catches: a ticket outliving the credential that minted it
    assert (await WS(ticket=t).open())["type"] == "websocket.close"


async def test_disconnect_lifetime_and_cap_release_resources(
    web, app_engine, owner_engine, monkeypatch
):
    monkeypatch.setattr(longpoll, "MAX_WAITERS", 1)
    org, user, h = await _pat_member(app_engine, owner_engine)
    redis, key = web_redis(web), longpoll.waiter_key(org, user)
    sock = WS(ticket=await ticket(web, h))
    assert (await sock.open())["type"] == "websocket.accept"
    # catches: unbounded sockets per user (the long-poll cap, shared)
    spare = await ticket(web, h)
    assert (await WS(ticket=spare).open())["type"] == "websocket.close"
    await sock.leave(2)  # catches: a gone client holding its slot until the lifetime ends
    assert await redis.zcard(key) == 0 and await _subscribers(redis) == []
    # catches: a cap refusal burning the ticket (the client retries with the same one)
    sock = WS(ticket=spare)
    assert (await sock.open())["type"] == "websocket.accept"
    await sock.leave(2)
    monkeypatch.setattr(ws, "LIFETIME", 0.6)
    sock = WS(ticket=await ticket(web, h))
    assert (await sock.open())["type"] == "websocket.accept"
    # catches: a socket living forever on a ticket minted once (the client reconnects with a fresh one)
    assert (await sock.closed(3))["code"] == 1000
    assert sock.task is not None
    await asyncio.wait_for(sock.task, 3)
    assert await redis.zcard(key) == 0 and await _subscribers(redis) == []


def test_a_socket_slot_outlives_its_socket():
    # catches: the shared cap's slot expiring while its socket still streams
    assert ws.SLOT_TTL > ws.LIFETIME + ws.SEND_TIMEOUT

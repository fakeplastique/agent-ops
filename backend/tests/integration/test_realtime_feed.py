import asyncio
import random
import time
import uuid

import pytest
from sqlalchemy import text

from app.authz.matrix import Role
from app.db.session import tenant_session
from app.realtime import longpoll
from app.realtime.projection import project
from app.services import outbox
from tests.api.harness import agent_auth, user_auth

from .helpers import cast, fetch, make_member, make_org, make_world, open_request, staff, voters

pytestmark = pytest.mark.anyio

EVENTS = "/api/v1/events"


async def poll(web, headers=None, after: int | str = 0, wait: int | str = 0, **params: str):
    return await web.client.get(
        EVENTS, params={"after": after, "wait": wait} | params, headers=headers or {}
    )


async def plant(app_engine, org, type_, payload) -> int:
    async with tenant_session(app_engine, org) as s:
        return await outbox.emit(s, org, type_, payload)


async def test_member_never_sees_events_of_requests_they_cannot_read(web, app_engine, owner_engine):
    a = await make_world(owner_engine)
    b = await make_world(owner_engine)
    for w in (a, b):
        req = await open_request(app_engine, w)
        for s in await voters(owner_engine, w, 2):
            await cast(app_engine, w, req, s)
    h = await user_auth(app_engine, b.org, await staff(owner_engine, b.org, "member"))
    res = await poll(web, h)
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["organization_id"] == str(b.org) and body["events"]
    a_ids = {str(r[0]) for r in await fetch(owner_engine, "select id from outbox_events where organization_id=:o", o=a.org)}  # fmt: skip
    assert str(a.org) not in res.text and not any(e["id"] in a_ids for e in body["events"])
    # catches: a feed broader than the REST read (every request it names reads back 200)
    for e in body["events"]:
        r = e["data"].get("decision_request_id") or e["data"].get("id")
        if e["type"].startswith(("decision.", "vote.", "round.", "message.")):
            got = await web.client.get(f"/api/v1/decision-requests/{r}", headers=h)
            assert got.status_code == 200, (e, got.text)


async def test_user_with_two_orgs_gets_only_selected_org_events(web, app_engine, owner_engine):
    one, two = await make_org(owner_engine), await make_org(owner_engine)
    await plant(app_engine, one, "plan.changed", {"organization_id": str(one), "from": "free", "to": "pro"})  # fmt: skip
    await plant(app_engine, two, "plan.changed", {"organization_id": str(two), "from": "free", "to": "pro"})  # fmt: skip
    email = f"{uuid.uuid4()}@x.test"
    await web.login(email)
    user = (await fetch(owner_engine, "select id from users where email=:e", e=email))[0][0]
    await make_member(owner_engine, one, user, "member")
    await make_member(owner_engine, two, user, "org_admin")
    assert (await web.post("/api/v1/auth/organization", json={"organization_id": str(one)})).status_code == 200  # fmt: skip
    # catches: org taken from the query string (or any input) instead of the session
    for extra in ({}, {"organization_id": str(two)}, {"org": str(two)}):
        body = (await poll(web, **extra)).json()
        assert body["organization_id"] == str(one), extra
        assert [e["data"]["organization_id"] for e in body["events"]] == [str(one)], extra
    assert (await web.post("/api/v1/auth/organization", json={"organization_id": str(two)})).status_code == 200  # fmt: skip
    body = (await poll(web)).json()
    assert body["organization_id"] == str(two)
    assert [e["data"]["organization_id"] for e in body["events"]] == [str(two)]


async def test_agent_token_cannot_follow_the_feed(web, app_engine, owner_engine):
    w = await make_world(owner_engine)
    res = await poll(web, await agent_auth(app_engine, w.org, w.identity))
    assert res.status_code == 403  # spec 5.3: the feed is for users; agents read through MCP


async def test_unknown_event_types_are_dropped_and_the_cursor_moves_past_them(
    web, app_engine, owner_engine
):
    w = await make_world(owner_engine)
    h = await user_auth(app_engine, w.org, await staff(owner_engine, w.org, "org_admin"))
    await plant(app_engine, w.org, "secret.thing", {"token": "tok-SECRET"})
    seq = await plant(app_engine, w.org, "plan.changed", {"organization_id": str(w.org), "from": "free", "to": "pro", "extra": "x-SECRET"})  # fmt: skip
    body = (await poll(web, h)).json()
    assert "SECRET" not in str(body)  # catches: a new type or field leaking by default
    assert [e["type"] for e in body["events"]] == ["plan.changed"] and body["after"] == seq


def test_projection_strips_blind_fields_for_every_role_and_keeps_round_two():
    blind = {"decision_request_id": "r", "session_id": "s", "round": 1, "request_version": 1,
             "cast": 1, "quorum": 3, "verdict": "reject", "confidence": 0.4, "rationale": "x"}  # fmt: skip
    for role in Role:  # catches: admin treated as exempt from blindness
        out = project("vote.cast", blind, role)
        assert out is not None and not {"verdict", "confidence", "rationale"} & out.keys(), role
        for odd in (True, "2", 2.0):  # True == 1 and 2.0 == 2 in Python: neither is round two
            got = project("vote.cast", blind | {"round": odd}, role)
            assert got is not None and "verdict" not in got, (role, odd)
    two = project("vote.cast", blind | {"round": 2}, Role.MEMBER)
    assert two is not None and (two["verdict"], two["confidence"]) == ("reject", 0.4)
    assert "rationale" not in two
    revealed = {"decision_request_id": "r", "round": 1, "request_version": 1,
                "votes": [{"session_id": "s", "verdict": "approve", "confidence": 0.9, "rationale": "x"}]}  # fmt: skip
    out = project("round.revealed", revealed, Role.MEMBER)
    assert out is not None and out["votes"] == [{"session_id": "s", "verdict": "approve", "confidence": 0.9}]  # fmt: skip
    assert project("nope", {}, Role.ORG_ADMIN) is None


async def _pat_in_new_org(app_engine, owner_engine, role="member"):
    org = await make_org(owner_engine)
    return org, await user_auth(app_engine, org, await staff(owner_engine, org, role))


async def test_after_cursor_replays_gapless_in_seq_order_under_concurrent_commits(
    web, app_engine, owner_engine
):
    org, h = await _pat_in_new_org(app_engine, owner_engine)
    n = 12
    committed = n - n // 4  # every fourth writer rolls back

    async def writer(i: int) -> None:
        try:
            async with tenant_session(app_engine, org) as s:
                await outbox.emit(
                    s, org, "plan.changed", {"organization_id": str(org), "to": str(i)}
                )
                await asyncio.sleep(random.uniform(0, 0.05))  # hold the counter lock a while
                if i % 4 == 0:
                    raise RuntimeError("rolled back")  # a rolled-back emit must leave no hole
        except RuntimeError:
            pass

    async def reader() -> list[int]:
        seen, after = [], 0
        while len(seen) < committed:
            body = (await poll(web, h, after=after, wait=1, limit="3")).json()
            seen += [e["seq"] for e in body["events"]]
            after = body["after"]
        return seen

    read = asyncio.create_task(asyncio.wait_for(reader(), 30))
    await asyncio.gather(*(writer(i) for i in range(1, n + 1)))
    # catches: a gap or reorder when a lower seq commits later, or a page boundary skipping one
    assert await read == list(range(1, committed + 1))
    for c in range(committed + 1):  # replay from every cursor
        body = (await poll(web, h, after=c)).json()
        assert [e["seq"] for e in body["events"]] == list(range(c + 1, committed + 1)), c


async def test_duplicate_delivery_is_the_same_seq(web, app_engine, owner_engine):
    org, h = await _pat_in_new_org(app_engine, owner_engine)
    for i in range(3):
        await plant(app_engine, org, "plan.changed", {"organization_id": str(org), "to": str(i)})
    first = (await poll(web, h)).json()["events"]
    again = (await poll(web, h)).json()["events"]
    overlap = (await poll(web, h, after=1)).json()["events"]
    # catches: clients unable to de-duplicate (a re-read names the same event the same way)
    assert first == again and overlap == first[1:]
    assert [e["seq"] for e in first] == [1, 2, 3] and len({e["id"] for e in first}) == 3


async def test_wait_bounded_25s_and_released_on_new_event(
    web, app_engine, owner_engine, monkeypatch
):
    org, h = await _pat_in_new_org(app_engine, owner_engine)
    bad = ({"wait": "26"}, {"wait": "-1"}, {"after": "-1"}, {"after": str(2**63)},
           {"limit": "0"}, {"limit": "101"}, {"after": "x"})  # fmt: skip
    for params in bad:
        assert (await web.client.get(EVENTS, params=params, headers=h)).status_code == 422, params
    t = time.monotonic()
    body = (await poll(web, h, after=0, wait=1)).json()
    assert body["events"] == [] and body["after"] == 0 and 0.9 <= time.monotonic() - t < 3
    # catches: stuck connections (released by the first event, not by the end of the wait)
    t = time.monotonic()
    waiting = asyncio.create_task(poll(web, h, after=0, wait=20))
    await asyncio.sleep(0.3)
    seq = await plant(app_engine, org, "plan.changed", {"organization_id": str(org), "to": "pro"})
    body = (await waiting).json()
    assert [e["seq"] for e in body["events"]] == [seq] and time.monotonic() - t < 3
    # The cap holds inside the loop too, whatever a caller passes.
    monkeypatch.setattr(longpoll, "MAX_WAIT", 0.2)
    t = time.monotonic()
    events, cursor = await asyncio.wait_for(
        longpoll.poll(app_engine, org, seq, 1000, 10, _never), 3
    )
    assert (events, cursor) == ([], seq) and time.monotonic() - t < 2


async def _never() -> Role:
    raise AssertionError("no batch, no recheck")


async def _lapse_mid_poll(web, app_engine, owner_engine, org, headers, lapse_sql, **params):
    """Start a waiting poll, run `lapse_sql`, then emit: the in-flight poll must not deliver."""
    polling, real = asyncio.Event(), longpoll.read_after

    async def spy(*args):
        polling.set()  # the request passed auth and is inside its wait loop
        return await real(*args)

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(longpoll, "read_after", spy)
        waiting = asyncio.create_task(poll(web, headers, after=0, wait=10))
        await asyncio.wait_for(polling.wait(), 10)
    async with owner_engine.begin() as c:
        await c.execute(text(lapse_sql), params)
    await plant(app_engine, org, "plan.changed", {"organization_id": str(org), "to": "x-AFTER"})
    return await waiting


async def test_membership_removed_mid_poll_next_poll_401_or_404(web, app_engine, owner_engine):
    org = await make_org(owner_engine)
    await staff(owner_engine, org, "org_admin")  # someone stays
    user = await staff(owner_engine, org, "member")
    h = await user_auth(app_engine, org, user)
    gone = "delete from memberships where organization_id=:o and user_id=:u"
    res = await _lapse_mid_poll(web, app_engine, owner_engine, org, h, gone, o=org, u=user)
    # catches: the feed outliving access (in flight: 404 and nothing; the PAT died with the membership)
    assert res.status_code == 404 and "x-AFTER" not in res.text
    assert (await poll(web, h)).status_code == 401


async def test_pat_revoked_mid_poll_delivers_nothing(web, app_engine, owner_engine):
    org, h = await _pat_in_new_org(app_engine, owner_engine)
    revoke = "update personal_access_tokens set revoked_at = now() where organization_id=:o"
    res = await _lapse_mid_poll(web, app_engine, owner_engine, org, h, revoke, o=org)
    assert res.status_code == 404 and "x-AFTER" not in res.text
    assert (await poll(web, h)).status_code == 401


async def test_cookie_member_removed_mid_poll_delivers_nothing(web, app_engine, owner_engine):
    org = await make_org(owner_engine)
    await staff(owner_engine, org, "org_admin")
    email = f"{uuid.uuid4()}@x.test"
    await web.login(email)
    user = (await fetch(owner_engine, "select id from users where email=:e", e=email))[0][0]
    await make_member(owner_engine, org, user, "member")
    await web.post("/api/v1/auth/organization", json={"organization_id": str(org)})
    gone = "delete from memberships where organization_id=:o and user_id=:u"
    res = await _lapse_mid_poll(web, app_engine, owner_engine, org, {}, gone, o=org, u=user)
    assert res.status_code == 404 and "x-AFTER" not in res.text
    nxt = await poll(web)
    # The session survives; its selected organization no longer resolves (deps.session_user).
    assert nxt.status_code == 403 and "x-AFTER" not in nxt.text


async def test_switching_organization_mid_poll_ends_the_old_feed(web, app_engine, owner_engine):
    one, two = await make_org(owner_engine), await make_org(owner_engine)
    email = f"{uuid.uuid4()}@x.test"
    await web.login(email)
    user = (await fetch(owner_engine, "select id from users where email=:e", e=email))[0][0]
    for org in (one, two):
        await make_member(owner_engine, org, user, "member")
    await web.post("/api/v1/auth/organization", json={"organization_id": str(one)})
    switch = "update web_sessions set selected_organization_id=:t where user_id=:u"
    res = await _lapse_mid_poll(web, app_engine, owner_engine, one, {}, switch, t=two, u=user)
    # catches: an in-flight poll still streaming the organization the user just left the view of
    assert res.status_code == 404 and "x-AFTER" not in res.text
    assert (await poll(web)).json()["organization_id"] == str(two)

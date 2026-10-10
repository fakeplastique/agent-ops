import asyncio
import uuid

import pytest
from sqlalchemy import text

from app.api import deps
from app.db.session import tenant_session
from app.main import app
from app.realtime import ws
from app.services import outbox
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
    # A switch after minting does not move the ticket; a query parameter cannot name another org.
    await web.post("/api/v1/auth/organization", json={"organization_id": str(two)})
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

import uuid

import pytest

from app.authz.matrix import Role
from app.db.session import tenant_session
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

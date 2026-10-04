import asyncio

import pytest

from tests.api.harness import BODY, DR, agent_auth, user_auth
from tests.integration.helpers import add_agent, fetch, make_world

pytestmark = pytest.mark.anyio

DTO_KEYS = {
    "id", "title", "description", "action_type", "payload", "risk_level", "effective_risk_level",
    "status", "version", "content_hash", "requested_by_agent_id", "requested_by_user_id",
    "voting_deadline_at", "escalation_reason", "decided_at", "created_at", "updated_at",
}  # fmt: skip


async def usage(owner, org) -> int:
    rows = await fetch(
        owner,
        "select value from usage_counters where organization_id=:o and metric='requests'",
        o=org,
    )
    return rows[0][0] if rows else 0


async def test_concurrent_create_same_key_one_request_one_usage_unit(web, app_engine, owner_engine):
    w = await make_world(owner_engine)
    h = await agent_auth(app_engine, w.org, w.identity) | {"Idempotency-Key": "k-1"}
    # catches: duplicate request and double metering (FM9)
    out = await asyncio.gather(*(web.client.post(DR, json=BODY, headers=h) for _ in range(6)))
    assert {r.status_code for r in out} == {201}
    assert all(r.json() == out[0].json() for r in out)  # jsonb reorders keys, values match
    assert len({r.headers["location"] for r in out}) == 1
    rows = await fetch(
        owner_engine, "select id from decision_requests where organization_id=:o", o=w.org
    )
    assert len(rows) == 1
    assert await usage(owner_engine, w.org) == 1
    # same key, other body: 422, still one request
    other = await web.client.post(DR, json=BODY | {"title": "Other"}, headers=h)
    assert other.status_code == 422 and other.json()["error"]["code"] == "idempotency_key_reuse"
    assert await usage(owner_engine, w.org) == 1


async def test_201_has_location_and_dto_hides_storage_fields(web, app_engine, owner_engine):
    w = await make_world(owner_engine)
    for h in (
        await agent_auth(app_engine, w.org, w.identity),
        await user_auth(app_engine, w.org, w.owner_user),
    ):
        r = await web.client.post(DR, json=BODY, headers=h)
        assert r.status_code == 201, r.text
        dto = r.json()
        # catches: storage model returned directly (snapshot, tenant id, session id, receipt)
        assert set(dto) == DTO_KEYS
        assert r.headers["location"] == f"{DR}/{dto['id']}"
        got = await web.client.get(r.headers["location"], headers=h)
        assert got.status_code == 200 and got.json() == dto
    assert dto["requested_by_user_id"] == str(w.owner_user) and dto["requested_by_agent_id"] is None
    # unknown fields are refused
    extra = await web.client.post(DR, json=BODY | {"status": "approved"}, headers=h)
    assert extra.status_code == 422


async def test_caps_title_200_payload_64k_message_4k(web, app_engine, owner_engine):
    w = await make_world(owner_engine, quorum=1)
    h = await agent_auth(app_engine, w.org, w.identity)
    ok = await web.client.post(DR, json=BODY | {"title": "t" * 200}, headers=h)
    assert ok.status_code == 201
    # catches: unbounded agent text
    long_title = await web.client.post(DR, json=BODY | {"title": "t" * 201}, headers=h)
    assert long_title.status_code == 422
    big = await web.client.post(DR, json=BODY | {"payload": {"x": "y" * 65_536}}, headers=h)
    assert big.status_code == 422 and big.json()["error"]["code"] == "invalid_input"
    # a message cap needs an open discussion: one reviewer vote puts the request in pending_approval
    req = ok.json()
    ident, _ = await add_agent(owner_engine, w)  # the requester's identity cannot vote
    vh = await agent_auth(app_engine, w.org, ident)
    vote = {"request_version": 1, "verdict": "approve", "confidence": 0.9, "rationale": "ok"}
    v = await web.client.post(f"{DR}/{req['id']}/votes", json=vote, headers=vh)
    assert v.status_code == 201, v.text
    msgs = f"{DR}/{req['id']}/messages"
    assert (await web.client.post(msgs, json={"body": "m" * 4096}, headers=h)).status_code == 201
    assert (await web.client.post(msgs, json={"body": "m" * 4097}, headers=h)).status_code == 422


async def test_revise_withdraw_vote_message_routes_map_service_errors(
    web, app_engine, owner_engine
):
    w = await make_world(owner_engine)
    agent = await agent_auth(app_engine, w.org, w.identity)
    user = await user_auth(app_engine, w.org, w.owner_user)
    voter = await agent_auth(app_engine, w.org, (await add_agent(owner_engine, w))[0])
    rid = (await web.client.post(DR, json=BODY, headers=agent)).json()["id"]
    one = f"{DR}/{rid}"
    vote = {"request_version": 1, "verdict": "approve", "confidence": 0.9}
    calls = [
        ("POST", f"{one}/revise", {"title": "x"}, user, 403, "forbidden"),
        ("POST", f"{one}/votes", vote, user, 403, "forbidden"),
        ("POST", f"{one}/votes", vote, agent, 403, "vote_not_allowed"),
        ("POST", f"{one}/votes", vote | {"request_version": 2}, voter, 409, "stale_version"),
        ("POST", f"{one}/votes", vote | {"confidence": True}, voter, 422, "invalid_input"),
        ("POST", f"{one}/votes", vote | {"confidence": 1.5}, voter, 422, "invalid_input"),
        ("POST", f"{one}/messages", {"body": "hi"}, voter, 409, "discussion_closed"),
        ("POST", f"{one}/revise", {"title": BODY["title"]}, agent, 422, "invalid_input"),
        ("POST", f"{one}/revise", {"title": "x"}, voter, 403, "forbidden"),
        ("POST", f"{one}/withdraw", None, user, 403, "forbidden"),  # a member, not org_admin
        ("POST", f"{one}/withdraw", None, agent, 200, None),
        ("POST", f"{one}/withdraw", None, agent, 409, "illegal_transition"),
        ("POST", f"{one}/revise", {"title": "x"}, agent, 409, "illegal_transition"),
        ("POST", f"{one}/votes", vote, voter, 409, "round_closed"),
        ("POST", f"{DR}/{w.org}/withdraw", None, agent, 404, "not_found"),
    ]
    # catches: 500 on illegal_transition and friends
    for method, path, body, h, status, code in calls:
        r = await web.client.request(method, path, json=body, headers=h)
        got = (r.status_code, r.json()["error"]["code"] if status >= 400 else None)
        assert got == (status, code), (path, body, r.text)
    assert (await web.client.get(one, headers=user)).json()["status"] == "cancelled"

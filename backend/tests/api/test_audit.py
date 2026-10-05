import pytest

from tests.api.harness import agent_auth, user_auth
from tests.integration.helpers import cast, make_world, open_request, voters

pytestmark = pytest.mark.anyio

AUDIT = "/api/v1/audit-logs"
KEYS = {"id", "entity_type", "entity_id", "action", "actor_type", "actor_id", "data", "created_at"}


async def test_audit_read_only_own_org_and_no_blind_verdicts(web, app_engine, owner_engine):
    w = await make_world(owner_engine, quorum=3)
    req = await open_request(app_engine, w)
    a, b, c = await voters(owner_engine, w, 3)
    await cast(app_engine, w, req, a, verdict="reject", confidence=0.42, rationale="secret-why")
    h = await user_auth(app_engine, w.org, w.owner_user)
    r = await web.client.get(AUDIT, params={"decision_request_id": str(req["id"])}, headers=h)
    assert r.status_code == 200, r.text
    items = r.json()["items"]
    assert all(set(i) == KEYS for i in items)  # no organization_id or storage columns
    actions = [(i["entity_type"], i["action"]) for i in items]
    assert ("decision_request", "created") in actions and ("vote", "cast") in actions
    # catches: audit exposing round-1 verdicts before the round is revealed
    vote = next(i for i in items if i["entity_type"] == "vote")
    assert not {"verdict", "confidence", "rationale"} & set(vote["data"])
    assert "reject" not in r.text and "0.42" not in r.text and "secret-why" not in r.text
    agent = await agent_auth(app_engine, w.org, w.identity)
    assert (await web.client.get(AUDIT, headers=agent)).json()["total"] >= len(items)

    # catches: tenant B reading tenant A's trail, by filter or by listing
    other = await make_world(owner_engine)
    bh = await user_auth(app_engine, other.org, other.owner_user)
    mine = await web.client.get(AUDIT, headers=bh)  # B's own trail (its token) only
    assert mine.status_code == 200 and str(req["id"]) not in mine.text
    assert not {i["id"] for i in items} & {i["id"] for i in mine.json()["items"]}
    got = await web.client.get(AUDIT, params={"decision_request_id": str(req["id"])}, headers=bh)
    assert got.status_code == 200 and got.json()["total"] == 0

    # once the round closes, the reveal row carries the votes
    for s in (b, c):
        await cast(app_engine, w, req, s)
    r = await web.client.get(AUDIT, params={"decision_request_id": str(req["id"])}, headers=h)
    assert any(i["action"] == "round_revealed" for i in r.json()["items"])
    assert "reject" in r.text

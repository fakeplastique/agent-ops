"""FM27: a downgrade keeps every policy and request as it was and fails closed on new requests."""

import pytest

from app.config import settings
from tests.api.harness import DR, agent_auth, user_auth

from .helpers import LEVELS3, fetch, make_world, open_request, staff

pytestmark = pytest.mark.anyio

REQ = {"title": "Refund", "description": "d", "action_type": "refund", "payload": {"amount": 5},
       "risk_level": "low"}  # fmt: skip


async def test_downgrade_keeps_data_and_blocks_create_with_policy_exceeds_plan(
    web, app_engine, owner_engine, monkeypatch
):
    w = await make_world(owner_engine, "pro", quorum=5, rounds=2, levels=LEVELS3)
    admin = await user_auth(app_engine, w.org, await staff(owner_engine, w.org, "org_admin"))
    agent = await agent_auth(app_engine, w.org, w.identity)
    req = await open_request(app_engine, w)
    policy_sql = "select quorum_size, rounds, levels from escalation_policies where id=:p"
    request_sql = "select status, version, policy_snapshot from decision_requests where id=:r"
    policy_before = await fetch(owner_engine, policy_sql, p=w.policy)
    request_before = await fetch(owner_engine, request_sql, r=req["id"])
    monkeypatch.setattr(settings, "demo_mode", True)
    r = await web.client.put(
        "/api/v1/organizations/current/plan", json={"plan": "free"}, headers=admin
    )
    assert r.status_code == 200 and r.json()["plan"] == "free", r.text
    # catches: clamping or deleting on downgrade (FM27)
    assert await fetch(owner_engine, policy_sql, p=w.policy) == policy_before
    assert await fetch(owner_engine, request_sql, r=req["id"]) == request_before
    r = await web.client.post(DR, json=REQ, headers=agent)
    assert r.status_code == 402 and r.json()["error"]["code"] == "policy_exceeds_plan", r.text
    count = "select count(*) from decision_requests where organization_id=:o"
    assert await fetch(owner_engine, count, o=w.org) == [(1,)]
    down = {"quorum_size": 3, "rounds": 1, "levels": [{"role": "manager", "timeout_minutes": 60}]}
    r = await web.client.patch(f"/api/v1/escalation-policies/{w.policy}", json=down, headers=admin)
    assert r.status_code == 200, r.text  # editing down is always allowed
    assert (await web.client.post(DR, json=REQ, headers=agent)).status_code == 201

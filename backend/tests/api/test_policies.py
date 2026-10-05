import pytest

from tests.api.harness import agent_auth, user_auth
from tests.integration.helpers import (
    _exec,
    fetch,
    make_member,
    make_org,
    make_team,
    make_user,
    staff,
)

pytestmark = pytest.mark.anyio

EP = "/api/v1/escalation-policies"
LEVEL = {"role": "manager", "timeout_minutes": 60}
GOOD = {"name": "default", "quorum_size": 3, "levels": [LEVEL], "risk_floors": {"refund": "high"}}
GOOD_FIELDS = GOOD | {
    "require_distinct_agents": True, "rounds": 1, "confidence_threshold": 0.8,
    "voting_timeout_minutes": 60,
}  # fmt: skip


async def _org(app_engine, owner_engine, plan="free"):
    """An organization without any policy, and its admin's PAT."""
    org = await make_org(owner_engine, plan)
    admin = await make_user(owner_engine)
    await make_member(owner_engine, org, admin, "org_admin")
    return org, await user_auth(app_engine, org, admin)


async def test_policy_crud(web, app_engine, owner_engine):
    org, admin = await _org(app_engine, owner_engine)
    made = await web.client.post(EP, json=GOOD, headers=admin)
    assert made.status_code == 201, made.text
    body = made.json()
    assert body["team_id"] is None and body["rounds"] == 1 and body["voting_timeout_minutes"] == 60
    assert made.headers["location"] == f"{EP}/{body['id']}"
    one = f"{EP}/{body['id']}"
    assert (await web.client.post(EP, json=GOOD, headers=admin)).status_code == 409  # one default
    patched = await web.client.patch(one, json={"quorum_size": 2}, headers=admin)
    assert patched.status_code == 200 and patched.json()["quorum_size"] == 2
    assert patched.json()["risk_floors"] == {"refund": "high"}
    member = await user_auth(app_engine, org, await staff(owner_engine, org, "member"))
    assert (await web.client.get(EP, headers=member)).json()["total"] == 1
    for h in (member, await user_auth(app_engine, org, await staff(owner_engine, org, "manager"))):
        assert (await web.client.patch(one, json={"quorum_size": 1}, headers=h)).status_code == 403
        assert (await web.client.delete(one, headers=h)).status_code == 403
    assert (await web.client.delete(one, headers=admin)).status_code == 204
    assert (await web.client.get(one, headers=admin)).status_code == 404


async def test_policy_above_plan_422_inline_details_and_team_policy_needs_flag_402(
    web, app_engine, owner_engine
):
    org, admin = await _org(app_engine, owner_engine, "free")
    two_levels = [LEVEL, {"role": "org_admin", "timeout_minutes": 30}]
    for over, limit, maximum in (
        ({"quorum_size": 4}, "quorum_size", 3),
        ({"rounds": 2}, "rounds", 1),
        ({"levels": two_levels}, "levels", 1),
    ):
        r = await web.client.post(EP, json=GOOD | over, headers=admin)
        # catches: plan limits enforced only in the UI (or clamped silently)
        assert r.status_code == 422, r.text
        err = r.json()["error"]
        assert err["code"] == "policy_above_plan"
        assert (err["details"]["limit"], err["details"]["max"], err["details"]["plan"]) == (
            limit,
            maximum,
            "free",
        )
    made = (await web.client.post(EP, json=GOOD, headers=admin)).json()
    r = await web.client.patch(f"{EP}/{made['id']}", json={"quorum_size": 9}, headers=admin)
    assert r.status_code == 422 and r.json()["error"]["details"]["limit"] == "quorum_size"
    team = await make_team(owner_engine, org)
    r = await web.client.post(EP, json=GOOD | {"team_id": str(team)}, headers=admin)
    # catches: a per-team policy on a plan without team_policies
    assert r.status_code == 402, r.text
    assert r.json()["error"]["details"] == {"flag": "team_policies", "required_plan": "enterprise"}
    await _exec(owner_engine, "update organizations set plan_key='enterprise' where id=:o", o=org)
    r = await web.client.post(
        EP, json=GOOD | {"team_id": str(team), "quorum_size": 9}, headers=admin
    )
    assert r.status_code == 201, r.text  # enterprise: any quorum, team policies on
    foreign = await make_team(owner_engine, await make_org(owner_engine, "enterprise"))
    # catches: a policy pinned to another tenant's team (409 or 500 would confirm it exists)
    assert (
        await web.client.post(EP, json=GOOD | {"team_id": str(foreign)}, headers=admin)
    ).status_code == 404
    # a policy's team is fixed at create
    r = await web.client.patch(f"{EP}/{r.json()['id']}", json={"team_id": None}, headers=admin)
    assert r.status_code == 422


async def test_risk_floors_and_levels_validated(web, app_engine, owner_engine):
    org, admin = await _org(app_engine, owner_engine, "pro")
    bad = [
        {"levels": [{"role": "member", "timeout_minutes": 5}]},  # role enum
        {"levels": [{"role": "ceo", "timeout_minutes": 5}]},
        {"levels": [{"role": "manager", "timeout_minutes": 0}]},  # timeouts > 0
        {"levels": [{"role": "manager", "timeout_minutes": -1}]},
        {"levels": [{"role": "manager", "timeout_minutes": True}]},
        {"levels": [{"role": "manager", "timeout_minutes": "5"}]},
        {"levels": [{"role": "manager", "timeout_minutes": 5, "skip": True}]},
        {"levels": []},
        {"voting_timeout_minutes": 0},
        {"quorum_size": 0},
        {"quorum_size": True},
        {"rounds": 3},
        {"confidence_threshold": 1.5},
        {"confidence_threshold": True},
        {"require_distinct_agents": "yes"},
        {"risk_floors": {"refund": "extreme"}},
        {"risk_floors": {"Refund": "high"}},  # floors key on the exact lowercase slug
        {"risk_floors": {"refund": 3}},
        {"risk_floors": ["refund"]},
        {"name": ""},
        {"name": "x" * 81},
        {"owner": "me"},  # unknown fields
    ]
    for over in bad:
        r = await web.client.post(EP, json=GOOD | over, headers=admin)
        # catches: a level with an unknown role or zero timeout (a step that never opens or never ends)
        assert r.status_code == 422, (over, r.text)
    assert (
        await fetch(
            owner_engine, "select 1 from escalation_policies where organization_id=:o", o=org
        )
    ) == []
    ok = GOOD | {"confidence_threshold": 1, "rounds": 2, "levels": [LEVEL, LEVEL]}
    assert (await web.client.post(EP, json=ok, headers=admin)).status_code == 201


async def test_agent_cannot_write_policies(web, app_engine, owner_engine):
    from tests.integration.helpers import make_world

    w = await make_world(owner_engine)
    h = await agent_auth(app_engine, w.org, w.identity)
    assert (await web.client.post(EP, json=GOOD, headers=h)).status_code == 403
    assert (await web.client.get(f"{EP}/{w.policy}", headers=h)).status_code == 200


async def test_concurrent_patches_keep_both_fields(app_engine, owner_engine):
    import asyncio

    from app.db.session import tenant_session
    from app.services import policies

    org, _ = await _org(app_engine, owner_engine, "pro")
    user = await make_user(owner_engine)
    async with tenant_session(app_engine, org) as s:
        made = await policies.create_policy(s, org, user, None, GOOD_FIELDS)

    async def patch(fields):
        async with tenant_session(app_engine, org) as s:
            await policies.update_policy(s, org, user, made["id"], fields)

    async with tenant_session(app_engine, org) as s1:
        await policies.update_policy(s1, org, user, made["id"], {"quorum_size": 5})
        other = asyncio.create_task(patch({"rounds": 2}))
        await asyncio.sleep(0.3)
    await other
    # catches: the second PATCH merging into the row it read before the first committed (lost update)
    rows = await fetch(
        owner_engine,
        "select quorum_size, rounds from escalation_policies where id=:i",
        i=made["id"],
    )
    assert rows == [(5, 2)]

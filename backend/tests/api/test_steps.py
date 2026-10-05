import pytest
from sqlalchemy import text

from tests.api.harness import agent_auth, user_auth
from tests.integration.helpers import (
    approval_world,
    fetch,
    make_member,
    make_team,
    make_world,
    pending_step,
    staff,
)

pytestmark = pytest.mark.anyio

STEPS = "/api/v1/approval-steps"
INBOX = "/api/v1/me/pending-approvals"
APPROVE = {"request_version": 1, "decision": "approve"}
REJECT = {"request_version": 1, "decision": "reject", "comment": "no"}


async def decided_rows(owner, req) -> list:
    return await fetch(
        owner,
        "select id, status from approval_steps where decision_request_id=:r"
        " and status in ('approved', 'rejected')",
        r=req["id"],
    )


async def test_decide_idempotent_key_replay_returns_first_and_conflicting_decision_409(
    web, app_engine, owner_engine
):
    # high risk: approving level 1 opens level 2, so a re-run (not a replay) would answer differently
    w, req, _ = await approval_world(owner_engine, app_engine, high=True)
    step, *_ = await pending_step(owner_engine, req)
    manager = await staff(owner_engine, w.org, "manager")
    h = await user_auth(app_engine, w.org, manager) | {"Idempotency-Key": "d-1"}
    url = f"{STEPS}/{step}/decide"
    first = await web.client.post(url, json=APPROVE, headers=h)
    assert first.status_code == 200, first.text
    level2, *_ = await pending_step(owner_engine, req)
    assert first.json() == {
        "step_id": str(step), "status": "approved", "request_status": "pending_approval",
        "next_step_id": str(level2),
    }  # fmt: skip
    # catches: double apply on retry (I10)
    again = await web.client.post(url, json=APPROVE, headers=h)
    assert (again.status_code, again.json()) == (200, first.json())
    other_body = await web.client.post(url, json=REJECT, headers=h)
    assert other_body.status_code == 422
    assert other_body.json()["error"]["code"] == "idempotency_key_reuse"
    reverse = await web.client.post(url, json=REJECT, headers={"Authorization": h["Authorization"]})
    assert reverse.status_code == 409 and reverse.json()["error"]["code"] == "already_decided"
    admin = await staff(owner_engine, w.org, "org_admin")
    late = await web.client.post(
        url, json=APPROVE, headers=await user_auth(app_engine, w.org, admin)
    )
    assert late.status_code == 409
    assert await decided_rows(owner_engine, req) == [(step, "approved")]
    assert (
        len(
            await fetch(
                owner_engine,
                "select id from approval_steps where decision_request_id=:r",
                r=req["id"],
            )
        )
        == 2
    )
    audits = await fetch(
        owner_engine,
        "select count(*) from audit_logs where entity_id=:s and action='decided'",
        s=step,
    )
    assert audits == [(1,)]


async def test_decide_stale_version_409_and_body_is_strict(web, app_engine, owner_engine):
    w, req, _ = await approval_world(owner_engine, app_engine)
    step, *_ = await pending_step(owner_engine, req)
    h = await user_auth(app_engine, w.org, await staff(owner_engine, w.org, "manager"))
    url = f"{STEPS}/{step}/decide"
    stale = await web.client.post(url, json=APPROVE | {"request_version": 2}, headers=h)
    assert stale.status_code == 409 and stale.json()["error"]["code"] == "stale_version"
    for bad in (APPROVE | {"request_version": True}, APPROVE | {"x": 1}, {"request_version": 1}):
        assert (await web.client.post(url, json=bad, headers=h)).status_code == 422, bad
    assert await decided_rows(owner_engine, req) == []


async def test_pending_approvals_lists_only_steps_i_may_decide_now(web, app_engine, owner_engine):
    w, req, team = await approval_world(owner_engine, app_engine, high=True)
    lead = await staff(owner_engine, w.org, "team_lead", team)
    other_lead = await staff(owner_engine, w.org, "team_lead", await make_team(owner_engine, w.org))
    manager = await staff(owner_engine, w.org, "manager")
    admin = await staff(owner_engine, w.org, "org_admin")
    member = await staff(owner_engine, w.org, "member", team)
    async with owner_engine.begin() as c:  # the requesting agent's owner may never decide it
        await c.execute(
            text("update memberships set role='org_admin' where organization_id=:o and user_id=:u"),
            {"o": w.org, "u": w.owner_user},
        )
    auth = {u: await user_auth(app_engine, w.org, u) for u in (lead, other_lead, manager, admin, member, w.owner_user)}  # fmt: skip

    async def inbox(user) -> list:
        r = await web.client.get(INBOX, headers=auth[user])
        assert r.status_code == 200, r.text
        assert r.json()["total"] == len(r.json()["items"])
        return [(i["id"], i["level"]) for i in r.json()["items"]]

    step1, *_ = await pending_step(owner_engine, req)
    # catches: showing steps the user cannot act on (other team, member, agent owner)
    assert await inbox(lead) == [(str(step1), 1)]
    assert await inbox(manager) == [(str(step1), 1)]
    assert await inbox(admin) == [(str(step1), 1)]
    for u in (other_lead, member, w.owner_user):
        assert await inbox(u) == [], u
    item = (await web.client.get(INBOX, headers=auth[lead])).json()["items"][0]
    assert item["title"] == "Refund order 7" and item["required_role"] == "team_lead"
    assert item["decision_request_id"] == str(req["id"]) and item["requested_by_agent_id"] == str(
        w.identity
    )
    r = await web.client.post(f"{STEPS}/{step1}/decide", json=APPROVE, headers=auth[lead])
    assert r.status_code == 200, r.text
    step2, level, _ = await pending_step(owner_engine, req)
    assert level == 2
    # catches: higher levels shown to roles below them, or to a user who already decided this version
    assert await inbox(lead) == []
    assert await inbox(other_lead) == []
    assert await inbox(manager) == [(str(step2), 2)]
    assert await inbox(admin) == [(str(step2), 2)]


async def test_pending_approvals_exclude_my_own_request_and_other_tenants(
    web, app_engine, owner_engine
):
    w, req, _ = await approval_world(owner_engine, app_engine)
    step, *_ = await pending_step(owner_engine, req)
    async with owner_engine.begin() as c:  # a user-created request: its creator never decides it
        await c.execute(
            text(
                "update decision_requests set requested_by_user_id=:u, requested_by_session_id=null"
                " where id=:r"
            ),
            {"u": (creator := await staff(owner_engine, w.org, "org_admin")), "r": req["id"]},
        )
    assert (
        await web.client.get(INBOX, headers=await user_auth(app_engine, w.org, creator))
    ).json()["items"] == []
    b = await make_world(owner_engine)
    b_admin = await staff(owner_engine, b.org, "org_admin")
    got = await web.client.get(INBOX, headers=await user_auth(app_engine, b.org, b_admin))
    assert got.json()["items"] == []
    admin = await staff(owner_engine, w.org, "org_admin")
    mine = await web.client.get(INBOX, headers=await user_auth(app_engine, w.org, admin))
    assert [i["id"] for i in mine.json()["items"]] == [str(step)]


async def test_agent_token_403_cross_tenant_404(web, app_engine, owner_engine):
    w, req, _ = await approval_world(owner_engine, app_engine)
    step, *_ = await pending_step(owner_engine, req)
    url = f"{STEPS}/{step}/decide"
    agent = await agent_auth(app_engine, w.org, w.identity)
    # catches: an agent deciding a human step (I8)
    r = await web.client.post(url, json=APPROVE, headers=agent)
    assert r.status_code == 403 and r.json()["error"]["code"] == "forbidden"
    assert (await web.client.get(INBOX, headers=agent)).status_code == 403
    b = await make_world(owner_engine)
    b_admin = await staff(owner_engine, b.org, "org_admin")
    # catches: wrong status revealing that tenant A's step exists
    r = await web.client.post(
        url, json=APPROVE, headers=await user_auth(app_engine, b.org, b_admin)
    )
    assert r.status_code == 404
    await make_member(owner_engine, b.org, w.owner_user, "org_admin")  # same person, other tenant
    r = await web.client.post(
        url, json=APPROVE, headers=await user_auth(app_engine, b.org, w.owner_user)
    )
    assert r.status_code == 404
    assert await decided_rows(owner_engine, req) == []

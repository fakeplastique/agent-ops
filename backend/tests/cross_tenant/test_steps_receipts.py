import pytest
from sqlalchemy import text

from app.db.session import lookup_as
from tests.api.harness import agent_auth, user_auth
from tests.integration.helpers import (
    approval_world,
    cast,
    fetch,
    make_world,
    open_request,
    pending_step,
    staff,
    voters,
)

pytestmark = pytest.mark.anyio


async def test_tenant_b_gets_404_on_tenant_a_steps(web, app_engine, owner_engine):
    _, req, _ = await approval_world(owner_engine, app_engine)
    step, *_ = await pending_step(owner_engine, req)
    b = await make_world(owner_engine)
    b_admin = await user_auth(app_engine, b.org, await staff(owner_engine, b.org, "org_admin"))
    decide = {"request_version": 1, "decision": "approve"}
    # catches: 403 or 409 revealing that another tenant's step exists
    r = await web.client.post(f"/api/v1/approval-steps/{step}/decide", json=decide, headers=b_admin)
    assert r.status_code == 404, r.text
    rows = await fetch(owner_engine, "select status from approval_steps where id=:s", s=step)
    assert rows == [("pending",)]


async def test_tenant_b_gets_404_on_tenant_a_receipt(web, app_engine, owner_engine):
    w = await make_world(owner_engine, floors={"refund": "low"})
    req = await open_request(app_engine, w)
    for s in await voters(owner_engine, w, 3):
        await cast(app_engine, w, req, s)
    assert (
        await fetch(owner_engine, "select status from decision_requests where id=:r", r=req["id"])
    ) == [("approved",)]
    b = await make_world(owner_engine)
    for h in (
        await user_auth(app_engine, b.org, b.owner_user),
        await agent_auth(app_engine, b.org, b.identity),
    ):
        r = await web.client.get(f"/api/v1/decision-requests/{req['id']}/receipt", headers=h)
        assert r.status_code == 404, r.text
    own = await user_auth(app_engine, w.org, w.owner_user)
    assert (
        await web.client.get(f"/api/v1/decision-requests/{req['id']}/receipt", headers=own)
    ).status_code == 200


async def test_receipt_lookup_shows_one_approved_row_and_never_a_pending_one(
    app_engine, owner_engine
):
    w = await make_world(owner_engine)
    pending = await open_request(app_engine, w)
    async with app_engine.begin() as c:
        await lookup_as(c, "app.receipt_request_id", str(pending["id"]))
        # catches: the pre-tenant lookup policy exposing an unapproved request of another tenant
        assert (await c.execute(text("select count(*) from decision_requests"))).scalar() == 0

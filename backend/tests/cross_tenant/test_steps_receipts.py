import pytest

from tests.api.harness import user_auth
from tests.integration.helpers import approval_world, fetch, make_world, pending_step, staff

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

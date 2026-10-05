import pytest

from app.db.session import tenant_session
from app.services.tokens import create_pat, issue_agent_token
from tests.integration.helpers import make_world, staff

pytestmark = pytest.mark.anyio


async def test_me_for_agent_token_pat_and_bad_credentials(web, app_engine, owner_engine):
    w = await make_world(owner_engine)
    user = await staff(owner_engine, w.org, "org_admin")
    async with tenant_session(app_engine, w.org) as s:
        agent_token = await issue_agent_token(s, w.org, w.identity)
        _, pat = await create_pat(s, w.org, user, "ci")

    agent = (
        await web.client.get("/api/v1/me", headers={"Authorization": f"Bearer {agent_token}"})
    ).json()
    me = (await web.client.get("/api/v1/me", headers={"Authorization": f"Bearer {pat}"})).json()
    assert agent == {
        "type": "agent",
        "id": str(w.identity),
        "organization_id": str(w.org),
        "role": None,
    }
    assert me == {
        "type": "user",
        "id": str(user),
        "organization_id": str(w.org),
        "role": "org_admin",
    }
    # catches: /me trusting anything but a live credential
    for header in ({}, {"Authorization": "Bearer pat_" + "x" * 43}):
        res = await web.client.get("/api/v1/me", headers=header)
        assert res.status_code == 401
        assert res.json()["error"]["code"] == "unauthenticated"

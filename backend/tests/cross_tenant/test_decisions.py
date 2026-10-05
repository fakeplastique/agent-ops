import pytest

from tests.api.harness import BODY, DR, agent_auth, user_auth
from tests.integration.helpers import fetch, make_world, open_request

pytestmark = pytest.mark.anyio


async def test_cross_tenant_ids_404_for_every_method(web, app_engine, owner_engine):
    a, b = await make_world(owner_engine), await make_world(owner_engine)
    req = await open_request(app_engine, a)
    b_agent = await agent_auth(app_engine, b.org, b.identity)
    b_user = await user_auth(app_engine, b.org, b.owner_user)
    one = f"{DR}/{req['id']}"
    vote = {"request_version": 1, "verdict": "approve", "confidence": 0.9}
    probes = [
        ("GET", one, None, b_user),
        ("POST", f"{one}/revise", {"title": "x"}, b_agent),
        ("POST", f"{one}/withdraw", None, b_agent),
        ("POST", f"{one}/withdraw", None, b_user),
        ("GET", f"{one}/votes", None, b_agent),
        ("POST", f"{one}/votes", vote, b_agent),
        ("GET", f"{one}/messages", None, b_user),
        ("POST", f"{one}/messages", {"body": "hi"}, b_user),
        ("POST", f"{one}/messages", {"body": "hi"}, b_agent),
    ]
    # catches: 403 or 409 revealing that another tenant's id exists
    for method, path, body, h in probes:
        r = await web.client.request(method, path, json=body, headers=h)
        assert (method, path, r.status_code) == (method, path, 404), r.text
    # and the list never shows it
    assert (await web.client.get(DR, headers=b_user)).json()["total"] == 0
    rows = await fetch(
        owner_engine, "select status, version from decision_requests where id=:r", r=req["id"]
    )
    assert rows == [("pending_votes", 1)]
    # B's own create still works, so the 404s are not a broken route
    assert (await web.client.post(DR, json=BODY, headers=b_agent)).status_code == 201

"""Spec 1.2 over realtime: tenant B's principals never receive tenant A's events, whatever they send."""

import uuid

import pytest

from tests.api.harness import user_auth
from tests.integration.helpers import cast, fetch, make_member, make_world, open_request, voters

pytestmark = pytest.mark.anyio


async def test_tenant_b_never_receives_tenant_a_events(web, app_engine, owner_engine):
    a, b = await make_world(owner_engine), await make_world(owner_engine)
    req = await open_request(app_engine, a)
    for s in await voters(owner_engine, a, 3):
        await cast(app_engine, a, req, s)
    a_seq = len(await fetch(owner_engine, "select 1 from outbox_events where organization_id=:o", o=a.org))  # fmt: skip
    pat = await user_auth(app_engine, b.org, b.owner_user)
    email = f"{uuid.uuid4()}@x.test"
    await web.login(email)
    user = (await fetch(owner_engine, "select id from users where email=:e", e=email))[0][0]
    await make_member(owner_engine, b.org, user, "org_admin")
    await web.post("/api/v1/auth/organization", json={"organization_id": str(b.org)})
    for headers in (pat, {}):  # a personal access token, then the web session cookie
        for params in ({}, {"organization_id": str(a.org)}, {"after": 0}, {"after": a_seq - 1}):
            res = await web.client.get(
                "/api/v1/events", params={"wait": 0} | params, headers=headers
            )
            assert res.status_code == 200, res.text
            assert res.json()["organization_id"] == str(b.org), params
            assert str(req["id"]) not in res.text and str(a.org) not in res.text, params

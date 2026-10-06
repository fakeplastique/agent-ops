import uuid

import pytest

from tests.api.harness import agent_auth, user_auth
from tests.integration.helpers import fetch, make_member, make_world, staff

pytestmark = pytest.mark.anyio

MT = "/api/v1/me/tokens"


async def _session_user(web, owner, org, role="member"):
    """Signs the web client in as a new member of org; returns the user id (the cookie is the credential)."""
    email = f"s-{uuid.uuid4()}@example.test"
    await web.login(email)
    user = (await fetch(owner, "select id from users where email=:e", e=email))[0][0]
    await make_member(owner, org, user, role)
    r = await web.post("/api/v1/auth/organization", json={"organization_id": str(org)})
    assert r.status_code == 200, r.text
    return user


async def test_pat_shown_once_listed_without_secrets_and_revoked(web, app_engine, owner_engine):
    w = await make_world(owner_engine)
    user = await _session_user(web, owner_engine, w.org)
    made = await web.post(MT, json={"name": "ci"})
    assert made.status_code == 201, made.text
    pat, token = made.json()["id"], made.json()["token"]
    assert token.startswith("pat_") and made.headers["location"] == f"{MT}/{pat}"
    new = {"Authorization": f"Bearer {token}"}
    assert (await web.client.get("/api/v1/me", headers=new)).json()["id"] == str(user)
    # catches: the secret (or its hash/prefix) readable after the one reply
    listed = await web.client.get(MT)
    one = await web.client.get(f"{MT}/{pat}", headers=new)
    for r in (listed, one):
        assert r.status_code == 200
        assert token not in r.text and token[4:12] not in r.text and "hash" not in r.text
    assert listed.json()["total"] == 1 and one.json()["name"] == "ci"
    assert (
        await web.client.request("DELETE", f"{MT}/{pat}", headers={"Origin": "https://app.test"})
    ).status_code == 204
    assert (await web.client.get("/api/v1/me", headers=new)).status_code == 401  # very next request
    assert (await web.client.get(f"{MT}/{pat}")).json()["revoked_at"] is not None
    revoked_at = (await web.client.get(f"{MT}/{pat}")).json()["revoked_at"]
    again = await web.client.request(
        "DELETE", f"{MT}/{pat}", headers={"Origin": "https://app.test"}
    )
    assert again.status_code == 204  # catches: a retried revoke reported as "not found"
    assert (await web.client.get(f"{MT}/{pat}")).json()[
        "revoked_at"
    ] == revoked_at  # first stamp kept


async def test_a_token_cannot_mint_tokens(web, app_engine, owner_engine):
    w = await make_world(owner_engine)
    pat = await user_auth(app_engine, w.org, w.owner_user)
    # catches: a leaked PAT minting fresh PATs that outlive its revocation
    r = await web.client.post(MT, json={"name": "persist"}, headers=pat)
    assert r.status_code in (401, 403), r.text
    rows = await fetch(owner_engine, "select 1 from personal_access_tokens where name='persist'")
    assert rows == []


async def test_one_user_cannot_see_or_revoke_anothers_token(web, app_engine, owner_engine):
    w = await make_world(owner_engine)
    from app.db.session import tenant_session
    from app.services.tokens import create_pat

    async with tenant_session(app_engine, w.org) as s:
        pat_id, raw = await create_pat(s, w.org, w.owner_user, "x")
    pat = {"id": str(pat_id), "token": raw}
    # even an org_admin of the same organization: a PAT belongs to its user
    admin = await user_auth(app_engine, w.org, await staff(owner_engine, w.org, "org_admin"))
    assert (await web.client.get(f"{MT}/{pat['id']}", headers=admin)).status_code == 404
    assert (await web.client.delete(f"{MT}/{pat['id']}", headers=admin)).status_code == 404
    assert pat["id"] not in (await web.client.get(MT, headers=admin)).text
    alive = {"Authorization": f"Bearer {pat['token']}"}
    assert (await web.client.get("/api/v1/me", headers=alive)).status_code == 200


async def test_agents_have_no_personal_tokens(web, app_engine, owner_engine):
    w = await make_world(owner_engine)
    agent = await agent_auth(app_engine, w.org, w.identity)
    assert (await web.client.post(MT, json={"name": "x"}, headers=agent)).status_code in (401, 403)
    assert (await web.client.get(MT, headers=agent)).status_code == 403
    await _session_user(web, owner_engine, w.org)
    for bad in ({"name": ""}, {"name": "x" * 81}, {"name": "a", "scopes": ["all"]}):
        assert (await web.post(MT, json=bad)).status_code == 422, bad
    assert (await web.post(MT, json={"name": "x"}, origin="https://evil.test")).status_code == 403

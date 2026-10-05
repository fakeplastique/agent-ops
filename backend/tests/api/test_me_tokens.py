import pytest

from tests.api.harness import agent_auth, user_auth
from tests.integration.helpers import make_world, staff

pytestmark = pytest.mark.anyio

MT = "/api/v1/me/tokens"


async def test_pat_shown_once_listed_without_secrets_and_revoked(web, app_engine, owner_engine):
    w = await make_world(owner_engine)
    me = await user_auth(app_engine, w.org, w.owner_user)
    made = await web.client.post(MT, json={"name": "ci"}, headers=me)
    assert made.status_code == 201, made.text
    pat, token = made.json()["id"], made.json()["token"]
    assert token.startswith("pat_") and made.headers["location"] == f"{MT}/{pat}"
    new = {"Authorization": f"Bearer {token}"}
    assert (await web.client.get("/api/v1/me", headers=new)).json()["id"] == str(w.owner_user)
    # catches: the secret (or its hash/prefix) readable after the one reply
    listed = await web.client.get(MT, headers=me)
    one = await web.client.get(f"{MT}/{pat}", headers=me)
    for r in (listed, one):
        assert r.status_code == 200
        assert token not in r.text and token[4:12] not in r.text and "hash" not in r.text
    assert listed.json()["total"] == 2 and one.json()["name"] == "ci"
    assert (await web.client.delete(f"{MT}/{pat}", headers=me)).status_code == 204
    assert (await web.client.get("/api/v1/me", headers=new)).status_code == 401  # very next request
    assert (await web.client.get(f"{MT}/{pat}", headers=me)).json()["revoked_at"] is not None
    assert (await web.client.delete(f"{MT}/{pat}", headers=me)).status_code == 404


async def test_one_user_cannot_see_or_revoke_anothers_token(web, app_engine, owner_engine):
    w = await make_world(owner_engine)
    mine = await user_auth(app_engine, w.org, w.owner_user)
    pat = (await web.client.post(MT, json={"name": "x"}, headers=mine)).json()
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
    assert (await web.client.post(MT, json={"name": "x"}, headers=agent)).status_code == 403
    assert (await web.client.get(MT, headers=agent)).status_code == 403
    me = await user_auth(app_engine, w.org, w.owner_user)
    for bad in ({"name": ""}, {"name": "x" * 81}, {"name": "a", "scopes": ["all"]}):
        assert (await web.client.post(MT, json=bad, headers=me)).status_code == 422, bad

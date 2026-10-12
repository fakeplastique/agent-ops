import pytest

from tests.api.harness import agent_auth, user_auth
from tests.integration.helpers import make_world, staff

pytestmark = pytest.mark.anyio

AG = "/api/v1/agents"


async def _admin(app_engine, owner_engine):
    w = await make_world(owner_engine)
    return w, await user_auth(app_engine, w.org, await staff(owner_engine, w.org, "org_admin"))


async def test_token_only_in_create_and_rotate_response_never_in_get_or_list(
    web, app_engine, owner_engine
):
    _, admin = await _admin(app_engine, owner_engine)
    made = await web.client.post(AG, json={"name": "bot", "vendor": "acme"}, headers=admin)
    assert made.status_code == 201, made.text
    agent, token = made.json()["id"], made.json()["token"]
    assert token.startswith("aop_") and made.headers["location"] == f"{AG}/{agent}"
    me = {"Authorization": f"Bearer {token}"}
    assert (await web.client.get("/api/v1/me", headers=me)).json()["id"] == agent
    # catches: token (or its hash/prefix) readable after the one reply
    for r in (
        await web.client.get(f"{AG}/{agent}", headers=admin),
        await web.client.get(AG, headers=admin),
    ):
        assert r.status_code == 200
        assert token not in r.text and token[4:12] not in r.text and "hash" not in r.text
    rotated = await web.client.post(f"{AG}/{agent}/rotate", headers=admin)
    assert rotated.status_code == 200 and rotated.json()["token"] != token
    # the old token dies on the very next request; the new one works
    assert (await web.client.get("/api/v1/me", headers=me)).status_code == 401
    new = {"Authorization": f"Bearer {rotated.json()['token']}"}
    assert (await web.client.get("/api/v1/me", headers=new)).status_code == 200
    assert (await web.client.delete(f"{AG}/{agent}", headers=admin)).status_code == 204
    assert (await web.client.get("/api/v1/me", headers=new)).status_code == 401
    assert (await web.client.get(f"{AG}/{agent}", headers=admin)).json()["status"] == "revoked"
    # a revoked identity cannot be brought back by rotating
    assert (await web.client.post(f"{AG}/{agent}/rotate", headers=admin)).status_code == 404


async def test_non_admin_cannot_create_rotate_revoke(web, app_engine, owner_engine):
    w, _ = await _admin(app_engine, owner_engine)
    callers = [await agent_auth(app_engine, w.org, w.identity)] + [
        await user_auth(app_engine, w.org, await staff(owner_engine, w.org, role))
        for role in ("member", "team_lead", "manager")
    ]
    one = f"{AG}/{w.identity}"
    for h in callers:
        # catches: a member (or an agent) minting or killing agent tokens
        assert (await web.client.post(AG, json={"name": "x"}, headers=h)).status_code == 403
        assert (await web.client.post(f"{one}/rotate", headers=h)).status_code == 403
        assert (await web.client.delete(one, headers=h)).status_code == 403
        assert (await web.client.get(one, headers=h)).status_code == 200  # reading is for everyone
    me = await agent_auth(app_engine, w.org, w.identity)
    assert (await web.client.get("/api/v1/me", headers=me)).status_code == 200


async def test_agent_input_validated(web, app_engine, owner_engine):
    w, admin = await _admin(app_engine, owner_engine)
    for body in (
        {"name": ""},
        {"name": "   "},  # whitespace-only reads as empty in every list
        {"name": "x" * 81},
        {"name": "a\x00b"},
        {"name": "ok", "token_hash": "f" * 64},
        {"name": "ok", "vendor": 5},
    ):
        r = await web.client.post(AG, json=body, headers=admin)
        assert r.status_code == 422, (body, r.text)
    stranger = {"name": "ok", "owner_user_id": str(w.identity)}  # no such member
    assert (await web.client.post(AG, json=stranger, headers=admin)).status_code == 404
    dup = {"name": "same"}
    assert (await web.client.post(AG, json=dup, headers=admin)).status_code == 201
    assert (await web.client.post(AG, json=dup, headers=admin)).status_code == 409
    owned = {"name": "owned", "owner_user_id": str(w.owner_user)}
    r = await web.client.post(AG, json=owned, headers=admin)
    assert r.status_code == 201 and r.json()["owner_user_id"] == str(w.owner_user)


async def test_revoked_agent_reports_when_it_was_revoked(web, app_engine, owner_engine):
    _, admin = await _admin(app_engine, owner_engine)
    agent = (await web.client.post(AG, json={"name": "bot"}, headers=admin)).json()
    assert (await web.client.delete(f"{AG}/{agent['id']}", headers=admin)).status_code == 204
    after = (await web.client.get(f"{AG}/{agent['id']}", headers=admin)).json()
    # catches: a revoked agent shown with no date, or the creation date presented as revocation
    assert after["status"] == "revoked" and after["updated_at"] > after["created_at"]

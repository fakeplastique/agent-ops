import pytest

from .helpers import make_member, make_org, staff
from .test_login import email, user_id

pytestmark = pytest.mark.anyio


async def test_lists_only_the_callers_own_memberships_with_role(web, owner_engine):
    addr = email()
    await web.login(addr)
    uid = await user_id(owner_engine, addr)
    mine_a = await make_org(owner_engine)
    mine_b = await make_org(owner_engine)
    foreign = await make_org(owner_engine)
    await make_member(owner_engine, mine_a, uid, "manager")
    await make_member(owner_engine, mine_b, uid, "member")
    await staff(owner_engine, foreign, "org_admin")  # someone else's organization

    r = await web.client.get("/api/v1/me/organizations")
    assert r.status_code == 200
    got = {o["id"]: o for o in r.json()["items"]}
    # catches: another tenant's organization (or another user's membership) in the switcher
    assert set(got) == {str(mine_a), str(mine_b)}
    assert got[str(mine_a)]["role"] == "manager" and got[str(mine_b)]["role"] == "member"
    assert set(got[str(mine_a)]) == {"id", "name", "role"}


async def test_no_membership_is_an_empty_list_and_no_session_is_401(web):
    await web.login(email())
    assert (await web.client.get("/api/v1/me/organizations")).json() == {"items": []}
    web.client.cookies.clear()
    r = await web.client.get("/api/v1/me/organizations")
    assert r.status_code == 401  # catches: an anonymous read of memberships

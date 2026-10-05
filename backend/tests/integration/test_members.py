import asyncio
import uuid

import pytest

from app.db.session import tenant_session
from app.domain.errors import LastOrgAdmin
from app.services.members import remove_member, set_role
from app.services.tokens import create_pat

from .helpers import fetch, make_org, staff

pytestmark = pytest.mark.anyio


async def test_last_org_admin_cannot_be_removed_or_demoted_even_concurrently(
    app_engine, owner_engine
):
    org = await make_org(owner_engine)
    a, b = await staff(owner_engine, org, "org_admin"), await staff(owner_engine, org, "org_admin")

    async def demote(target):
        async with tenant_session(app_engine, org) as s:
            await set_role(s, org, target, "member")

    # catches: two admins demoting each other
    out = await asyncio.gather(demote(a), demote(b), return_exceptions=True)
    assert sum(isinstance(o, LastOrgAdmin) for o in out) == 1 and sum(o is None for o in out) == 1
    left = await fetch(
        owner_engine,
        "select user_id from memberships where organization_id=:o and role='org_admin'",
        o=org,
    )
    assert len(left) == 1
    sole = left[0][0]
    async with tenant_session(app_engine, org) as s:
        with pytest.raises(LastOrgAdmin):
            await remove_member(s, org, sole)
    async with tenant_session(app_engine, org) as s:
        with pytest.raises(LastOrgAdmin):
            await set_role(s, org, sole, "manager")


async def test_only_org_admin_invites_and_changes_roles(web, app_engine, owner_engine):
    org = await make_org(owner_engine)
    mgr = await staff(owner_engine, org, "manager")
    victim = await staff(owner_engine, org, "member")
    admin = await staff(owner_engine, org, "org_admin")
    async with tenant_session(app_engine, org) as s:
        _, mgr_pat = await create_pat(s, org, mgr, "m")
        _, admin_pat = await create_pat(s, org, admin, "a")
    h = lambda t: {"Authorization": f"Bearer {t}"}
    # catches: manager escalating a role
    assert (
        await web.client.post(
            "/api/v1/invitations",
            headers=h(mgr_pat),
            json={"email": "a@example.test", "role": "member"},
        )
    ).status_code == 403
    assert (
        await web.client.patch(
            f"/api/v1/members/{victim}", headers=h(mgr_pat), json={"role": "org_admin"}
        )
    ).status_code == 403
    assert (
        await web.client.patch(
            f"/api/v1/members/{mgr}", headers=h(mgr_pat), json={"role": "org_admin"}
        )
    ).status_code == 403
    assert (
        await web.client.delete(f"/api/v1/members/{victim}", headers=h(mgr_pat))
    ).status_code == 403
    ok = await web.client.patch(
        f"/api/v1/members/{victim}", headers=h(admin_pat), json={"role": "team_lead"}
    )
    assert ok.status_code == 200 and ok.json()["role"] == "team_lead"
    listed = (
        await web.client.get("/api/v1/members", headers=h(mgr_pat))
    ).json()  # reading is for every member
    assert {m["user_id"] for m in listed["items"]} == {str(mgr), str(victim), str(admin)}
    assert (
        await web.client.delete(f"/api/v1/members/{victim}", headers=h(admin_pat))
    ).status_code == 204
    assert (
        await web.client.delete(f"/api/v1/members/{admin}", headers=h(admin_pat))
    ).status_code == 409


async def test_first_user_org_creation_makes_org_admin_and_writes_audit(web, owner_engine):
    await web.login("founder@example.test")
    uid = (await fetch(owner_engine, "select id from users where email='founder@example.test'"))[0][
        0
    ]
    # catches: org without an admin
    r = await web.post("/api/v1/organizations", json={"name": "Acme Inc", "slug": "acme-inc"})
    assert r.status_code == 201, r.text
    org = uuid.UUID(r.json()["id"])
    assert await fetch(
        owner_engine,
        "select role from memberships where organization_id=:o and user_id=:u",
        o=org,
        u=uid,
    ) == [("org_admin",)]
    audit = await fetch(
        owner_engine, "select action, actor_id from audit_logs where organization_id=:o", o=org
    )
    assert ("organization_created", uid) in audit
    sess = (await web.client.get("/api/v1/auth/session")).json()
    assert sess["organization_id"] == str(org) and sess["role"] == "org_admin"
    dup = await web.post("/api/v1/organizations", json={"name": "Other", "slug": "acme-inc"})
    assert dup.status_code == 409
    for bad in ({"name": "", "slug": "ok-slug"}, {"name": "N", "slug": "Bad Slug!"}):
        assert (await web.post("/api/v1/organizations", json=bad)).status_code == 422
    no_origin = await web.post(
        "/api/v1/organizations", origin=None, json={"name": "N", "slug": "zz-slug"}
    )
    assert no_origin.status_code == 403


async def test_tenant_b_cannot_touch_tenant_a_ids(web, app_engine, owner_engine):
    a, b = await make_org(owner_engine), await make_org(owner_engine)
    a_member = await staff(owner_engine, a, "member")
    await staff(owner_engine, a, "org_admin")
    b_admin = await staff(owner_engine, b, "org_admin")
    async with tenant_session(app_engine, b) as s:
        _, pat = await create_pat(s, b, b_admin, "b")
    h = {"Authorization": f"Bearer {pat}"}
    # catches: an id from another tenant reaching a row
    assert (await web.client.get(f"/api/v1/organizations/{a}", headers=h)).status_code == 404
    assert (
        await web.client.patch(f"/api/v1/members/{a_member}", headers=h, json={"role": "manager"})
    ).status_code == 404
    assert (await web.client.delete(f"/api/v1/members/{a_member}", headers=h)).status_code == 404
    assert (
        await web.client.delete(f"/api/v1/invitations/{uuid.uuid4()}", headers=h)
    ).status_code == 404
    assert await fetch(
        owner_engine, "select role from memberships where user_id=:u", u=a_member
    ) == [("member",)]

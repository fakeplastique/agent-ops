import asyncio
import uuid
from datetime import timedelta

import pytest
from sqlalchemy import text

from app.db.session import tenant_session
from app.domain.errors import AlreadyMember, InvitationUnusable
from app.services.invitations import accept_invitation, create_invitation

from .helpers import FakeClock, fetch, make_member, make_org, make_user, utc
from .web_harness import ORIGIN

pytestmark = pytest.mark.anyio


def addr() -> str:
    return f"invitee-{uuid.uuid4()}@example.test"


async def invite(app_engine, owner, org, email, role="manager", clock=None):
    admin = await make_user(owner)
    await make_member(owner, org, admin, "org_admin")
    async with tenant_session(app_engine, org) as s:
        return await create_invitation(
            s, org, admin, email, role, None, clock or FakeClock(utc(2026, 1, 1))
        )


async def user_with_email(owner, email) -> uuid.UUID:
    uid = await make_user(owner)
    async with owner.begin() as c:
        await c.execute(text("update users set email=:e where id=:i"), {"e": email, "i": uid})
    return uid


async def test_accept_twice_concurrent_one_membership(app_engine, owner_engine):
    org, email, clock = await make_org(owner_engine), addr(), FakeClock(utc(2026, 1, 1))
    _, raw = await invite(app_engine, owner_engine, org, email)
    user = await user_with_email(owner_engine, email)
    # catches: SELECT then UPDATE letting both through
    out = await asyncio.gather(
        *(accept_invitation(app_engine, user, raw, clock) for _ in range(6)),
        return_exceptions=True,
    )
    assert sum(not isinstance(o, Exception) for o in out) == 1
    assert all(
        isinstance(o, InvitationUnusable | AlreadyMember) for o in out if isinstance(o, Exception)
    )
    rows = await fetch(
        owner_engine,
        "select role from memberships where organization_id=:o and user_id=:u",
        o=org,
        u=user,
    )
    assert rows == [("manager",)]


async def test_expired_used_revoked_unknown_same_message_and_status(web, app_engine, owner_engine):
    org, email = await make_org(owner_engine), addr()
    t0 = web.clock.now()
    _, ok_raw = await invite(app_engine, owner_engine, org, email, clock=web.clock)
    _, old_raw = await invite(
        app_engine, owner_engine, org, email, clock=FakeClock(t0 - timedelta(days=8))
    )
    used, used_raw = await invite(app_engine, owner_engine, org, email, clock=web.clock)
    gone, gone_raw = await invite(app_engine, owner_engine, org, email, clock=web.clock)
    async with owner_engine.begin() as c:
        await c.execute(text("update invitations set accepted_at=now() where id=:i"), {"i": used})
        await c.execute(text("update invitations set revoked_at=now() where id=:i"), {"i": gone})
    await web.login(email)
    seen = set()
    # catches: invitation state oracle
    for raw in (old_raw, used_raw, gone_raw, "inv_" + "z" * 43, "garbage"):
        r = await web.post("/api/v1/invitations/accept", json={"token": raw})
        seen.add((r.status_code, str(r.json())))
    assert len(seen) == 1 and next(iter(seen))[0] == 404
    assert (await web.post("/api/v1/invitations/accept", json={"token": ok_raw})).status_code == 200


async def test_verified_email_must_equal_invited_email(web, app_engine, owner_engine):
    org, email = await make_org(owner_engine), addr()
    _, raw = await invite(app_engine, owner_engine, org, email, clock=web.clock)
    await web.login(addr())
    # catches: a leaked link granting the role to anyone
    r = await web.post("/api/v1/invitations/accept", json={"token": raw})
    assert r.status_code == 403 and r.json()["error"]["code"] == "invitation_email_mismatch"
    assert (
        await fetch(
            owner_engine,
            "select 1 from memberships where organization_id=:o and role='manager'",
            o=org,
        )
        == []
    )
    await web.client.post("/api/v1/auth/logout", headers={"Origin": ORIGIN})
    await web.login(
        email.upper()
    )  # the right account can still use it: a mismatch consumed nothing
    r = await web.post("/api/v1/invitations/accept", json={"token": raw})
    assert r.status_code == 200 and r.json()["role"] == "manager"
    assert (await web.client.get("/api/v1/auth/session")).json()["organization_id"] == str(org)


async def test_token_stored_hashed_7_day_expiry_with_clock(app_engine, owner_engine):
    org, email, t0 = await make_org(owner_engine), addr(), utc(2026, 1, 1)
    inv, raw = await invite(app_engine, owner_engine, org, email, clock=FakeClock(t0))
    # catches: plaintext token or no expiry
    row = (
        await fetch(
            owner_engine,
            "select to_jsonb(i)::text, expires_at from invitations i where id=:i",
            i=inv,
        )
    )[0]
    assert raw not in row[0] and row[1] == t0 + timedelta(days=7)
    logs = await fetch(
        owner_engine, "select data::text from audit_logs where organization_id=:o", o=org
    )
    assert raw not in str(logs)
    user = await user_with_email(owner_engine, email)
    with pytest.raises(InvitationUnusable):
        await accept_invitation(app_engine, user, raw, FakeClock(t0 + timedelta(days=7, seconds=1)))
    await accept_invitation(
        app_engine, user, raw, FakeClock(t0 + timedelta(days=7) - timedelta(seconds=1))
    )


async def test_rest_invite_flow_and_revoke(web, owner_engine):
    org = await make_org(owner_engine)
    await web.login("boss@example.test")
    uid = (await fetch(owner_engine, "select id from users where email='boss@example.test'"))[0][0]
    await make_member(owner_engine, org, uid, "org_admin")
    await web.post("/api/v1/auth/organization", json={"organization_id": str(org)})
    r = await web.post("/api/v1/invitations", json={"email": "New@Example.test", "role": "member"})
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["token"].startswith("inv_") and body["email"] == "new@example.test"
    listed = (await web.client.get("/api/v1/invitations")).json()
    assert [i["id"] for i in listed["items"]] == [body["id"]] and "token" not in listed["items"][0]
    d = await web.client.delete(f"/api/v1/invitations/{body['id']}", headers={"Origin": ORIGIN})
    assert d.status_code == 204
    assert (await web.client.get("/api/v1/invitations")).json()["items"] == []
    bad = await web.post("/api/v1/invitations", json={"email": "x@example.test", "role": "root"})
    assert bad.status_code == 422


async def test_existing_member_gets_409_and_the_link_is_not_burned(app_engine, owner_engine):
    org, email, clock = await make_org(owner_engine), addr(), FakeClock(utc(2026, 1, 1))
    inv, raw = await invite(app_engine, owner_engine, org, email)
    user = await user_with_email(owner_engine, email)
    await make_member(owner_engine, org, user, "member")
    # catches: an acceptance recorded for someone who was already in
    with pytest.raises(AlreadyMember):
        await accept_invitation(app_engine, user, raw, clock)
    rows = await fetch(owner_engine, "select accepted_at from invitations where id=:i", i=inv)
    assert rows == [(None,)]
    assert await fetch(owner_engine, "select role from memberships where user_id=:u", u=user) == [
        ("member",)
    ]


async def test_accepted_invitation_cannot_be_accepted_again_by_another_session(
    app_engine, owner_engine
):
    org, email, clock = await make_org(owner_engine), addr(), FakeClock(utc(2026, 1, 1))
    _, raw = await invite(app_engine, owner_engine, org, email)
    user = await user_with_email(owner_engine, email)
    await accept_invitation(app_engine, user, raw, clock)
    async with owner_engine.begin() as c:
        await c.execute(text("delete from memberships where user_id=:u"), {"u": user})
    # catches: reuse after the member left
    with pytest.raises(InvitationUnusable):
        await accept_invitation(app_engine, user, raw, clock)

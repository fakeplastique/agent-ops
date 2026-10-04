import hashlib

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from tests.integration.helpers import fetch, make_user

pytestmark = pytest.mark.anyio


async def refused(engine, sql, **p) -> bool:
    try:
        async with engine.begin() as c:
            await c.execute(text(sql), p)
    except DBAPIError:
        return True
    return False


async def test_a_session_row_is_visible_only_under_its_own_hash(app_engine, owner_engine):
    user = await make_user(owner_engine)
    h1, h2 = hashlib.sha256(b"one").hexdigest(), hashlib.sha256(b"two").hexdigest()
    async with owner_engine.begin() as c:
        for h in (h1, h2):
            await c.execute(
                text(
                    "insert into web_sessions (id_hash, user_id, last_seen_at, expires_at) values (:h, :u, now(), now() + interval '1 day')"
                ),
                {"h": h, "u": user},
            )
    async with app_engine.begin() as c:
        none = (await c.execute(text("select count(*) from web_sessions"))).scalar_one()
        await c.execute(text("select set_config('app.session_hash', :h, true)"), {"h": h1})
        seen = (await c.execute(text("select id_hash from web_sessions"))).all()
    assert none == 0 and seen == [(h1,)]  # catches: sessions readable without the cookie's hash


async def test_the_session_grants_cannot_repoint_a_session(app_engine, owner_engine):
    user, other = await make_user(owner_engine), await make_user(owner_engine)
    h = hashlib.sha256(b"x").hexdigest()
    async with owner_engine.begin() as c:
        await c.execute(
            text(
                "insert into web_sessions (id_hash, user_id, last_seen_at, expires_at) values (:h, :u, now(), now() + interval '1 day')"
            ),
            {"h": h, "u": user},
        )
    for col, val in (
        ("user_id", other),
        ("expires_at", "2999-01-01"),
        ("id_hash", "a" * 64),
        ("created_at", "2000-01-01"),
    ):
        async with app_engine.begin() as c:
            await c.execute(text("select set_config('app.session_hash', :h, true)"), {"h": h})
            try:
                await c.execute(
                    text(f"update web_sessions set {col} = :v"),
                    {
                        "v": val
                        if col != "expires_at" and col != "created_at"
                        else __import__("datetime").datetime.fromisoformat(val)
                    },
                )
            except DBAPIError:
                continue
            raise AssertionError(
                f"app_rt updated web_sessions.{col}"
            )  # catches: a session re-pointed at another user


async def test_login_lookup_and_create_need_the_matching_email_setting(app_engine, owner_engine):
    addr = "login-probe@example.test"
    async with app_engine.begin() as c:
        assert (
            await c.execute(text("select count(*) from users"))
        ).scalar_one() == 0  # no tenant, no email: nothing
    ins = "insert into users (email, full_name) values (:e, 'x')"
    assert await refused(app_engine, ins, e=addr)  # catches: any session creating users at will
    async with app_engine.begin() as c:
        await c.execute(text("select set_config('app.login_email', :e, true)"), {"e": addr})
        await c.execute(text(ins), {"e": addr})
        assert (await c.execute(text("select count(*) from users"))).scalar_one() == 1
    async with app_engine.begin() as c:
        await c.execute(text("select set_config('app.login_email', :e, true)"), {"e": addr})
        with pytest.raises(DBAPIError):
            await c.execute(
                text(ins), {"e": "someone-else@example.test"}
            )  # the setting names exactly one email
    assert await fetch(owner_engine, "select platform_admin from users where email=:e", e=addr) == [
        (False,)
    ]


async def test_users_cannot_be_promoted_or_edited_by_the_runtime_role(app_engine, owner_engine):
    addr = "promote-probe@example.test"
    async with app_engine.begin() as c:
        await c.execute(text("select set_config('app.login_email', :e, true)"), {"e": addr})
        await c.execute(text("insert into users (email, full_name) values (:e, 'x')"), {"e": addr})
    # catches: self-promotion to platform admin, an email swap, or deleting a user around the service
    for sql in (
        "update users set platform_admin = true",
        "update users set email = 'x@y.z'",
        "delete from users",
    ):
        assert await refused_with_login(app_engine, addr, sql), sql
    insert_admin = "insert into users (email, full_name, platform_admin) values ('admin-probe@example.test', 'x', true)"
    assert await refused_with_login(app_engine, "admin-probe@example.test", insert_admin)
    assert await fetch(owner_engine, "select platform_admin from users where email=:e", e=addr) == [
        (False,)
    ]


async def refused_with_login(engine, addr, sql) -> bool:
    try:
        async with engine.begin() as c:
            await c.execute(text("select set_config('app.login_email', :e, true)"), {"e": addr})
            await c.execute(text(sql))
    except DBAPIError:
        return True
    return False

import hashlib
import uuid
from datetime import timedelta
from urllib.parse import parse_qs, urlparse

import pytest
from sqlalchemy import text

from app.config import settings

from .helpers import fetch, make_member, make_org
from .web_harness import ORIGIN

pytestmark = pytest.mark.anyio


STATE_COOKIE = "aop_oidc_state"


def bind_browser(web, state: str) -> None:
    """Pretend this browser started the flow with this state (what /login's cookie does)."""
    web.client.cookies.set(STATE_COOKIE, state, domain="app.test", path="/api/v1/auth")


def state_of(location: str) -> str:
    return parse_qs(urlparse(location).query)["state"][0]


def email() -> str:
    return f"user-{uuid.uuid4()}@example.test"


async def user_id(owner, addr):
    return (await fetch(owner, "select id from users where email=:e", e=addr.lower()))[0][0]


async def session_info(web):
    return await web.client.get("/api/v1/auth/session")


async def test_login_creates_the_user_once_and_opens_a_session(web, owner_engine):
    addr = email()
    r = await web.login(addr.upper(), name="Ada Lovelace")
    assert r.status_code == 302 and r.headers["location"] == ORIGIN + "/"
    rows = await fetch(
        owner_engine,
        "select email, full_name, platform_admin from users where email=:e",
        e=addr.lower(),
    )
    assert rows == [
        (addr.lower(), "Ada Lovelace", False)
    ]  # lower-cased; never an admin from a login
    me = await session_info(web)
    assert (
        me.status_code == 200
        and me.json()["user_id"] == str(await user_id(owner_engine, addr))
        and me.json()["organization_id"] is None
    )
    await web.login(addr)  # the same person again
    assert len(await fetch(owner_engine, "select 1 from users where email=:e", e=addr.lower())) == 1


async def test_the_login_redirect_carries_state_nonce_and_pkce(web):
    r = await web.client.get("/api/v1/auth/login")
    assert r.status_code == 302
    url = urlparse(r.headers["location"])
    q = {k: v[0] for k, v in parse_qs(url.query).items()}
    assert url.netloc == "issuer.test" and q["scope"] == "openid email profile"
    assert q["code_challenge_method"] == "S256" and len(q["state"]) >= 24 and len(q["nonce"]) >= 24
    assert q["redirect_uri"] == f"{ORIGIN}/api/v1/auth/callback" and len(q["code_challenge"]) == 43


async def test_unverified_email_refused(web, owner_engine):
    addr = email()
    r = await web.login(
        addr, email_verified=False
    )  # catches: an unverified Google address creating a user
    assert r.status_code == 400 and r.json()["error"]["code"] == "login_failed"
    assert await fetch(owner_engine, "select 1 from users where email=:e", e=addr) == []
    assert "set-cookie" not in r.headers and (await session_info(web)).status_code == 401
    for bad in ("true", 1, None):  # only the boolean true counts
        assert (await web.login(email(), email_verified=bad)).status_code == 400


async def test_missing_or_blank_email_refused(web):
    for claims in (
        {"email": ""},
        {"email": None},
        {"email": "no-at-sign"},
        {"email": "a\x00b@example.test"},
        {"email": "a b@example.test"},
        {"email": "a\n@example.test"},
    ):
        web.fake.override = claims
        assert (await web.login("x@example.test")).status_code == 400


async def test_state_and_pkce_mismatch_refused(web):
    start = await web.client.get("/api/v1/auth/login")
    callback = web.fake.authorize(start.headers["location"], email())
    path = callback.removeprefix(ORIGIN)
    state = state_of(start.headers["location"])
    forged = path.replace("state=", "state=x")
    bind_browser(web, "x" + state)
    assert (await web.client.get(forged)).status_code == 400  # a state we never issued
    assert (
        await web.client.get("/api/v1/auth/callback?code=abc")
    ).status_code == 400  # no state at all
    bind_browser(web, state)
    ok = await web.client.get(path)
    assert ok.status_code == 302
    replay = web.fake.authorize(start.headers["location"], email())  # the state is single use
    bind_browser(web, state)
    assert (await web.client.get(replay.removeprefix(ORIGIN))).status_code == 400
    # code injection: a code minted for flow B redeemed with flow A's state (and so A's PKCE verifier)
    a = await web.client.get("/api/v1/auth/login")
    b = await web.client.get("/api/v1/auth/login")
    code_b = web.fake.authorize(b.headers["location"], email()).split("code=")[1].split("&")[0]
    state_a = state_of(a.headers["location"])
    bind_browser(
        web, state_a
    )  # this browser did start flow A: only PKCE can stop the injected code
    injected = await web.client.get(f"/api/v1/auth/callback?code={code_b}&state={state_a}")
    assert (
        injected.status_code == 400 and "set-cookie" not in injected.headers
    )  # PKCE: the verifier does not match


async def test_login_csrf_a_callback_planted_in_another_browser_is_refused(web, owner_engine):
    import httpx

    from app.main import app

    addr = email()
    start = await web.client.get("/api/v1/auth/login")  # the attacker's browser starts a login...
    planted = web.fake.authorize(start.headers["location"], addr).removeprefix(
        ORIGIN
    )  # ...and keeps the callback URL
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=ORIGIN) as victim:
        r = await victim.get(planted)  # the victim's browser is tricked into visiting it
        assert (
            r.status_code == 400 and r.json()["error"]["code"] == "login_failed"
        )  # catches: login CSRF
        assert "set-cookie" not in r.headers and victim.cookies.get("aop_session") is None
    assert await fetch(owner_engine, "select 1 from users where email=:e", e=addr) == []
    assert (
        await web.client.get(planted)
    ).status_code == 302  # the attacker's own browser is unaffected: nothing was burned


async def test_a_state_cookie_from_another_flow_is_refused(web):
    a = await web.client.get("/api/v1/auth/login")
    b = await web.client.get("/api/v1/auth/login")  # the cookie jar now holds flow B's state
    callback_a = web.fake.authorize(a.headers["location"], email()).removeprefix(ORIGIN)
    assert (await web.client.get(callback_a)).status_code == 400  # A's code with B's cookie
    bind_browser(web, state_of(a.headers["location"]))
    assert (await web.client.get(callback_a)).status_code == 302  # a mismatch did not burn flow A
    assert b


async def test_the_state_cookie_is_httponly_secure_lax_short_and_cleared_after_login(web):
    start = await web.client.get("/api/v1/auth/login")
    cookie = start.headers["set-cookie"].lower()
    assert (
        "aop_oidc_state=" in cookie
        and "httponly" in cookie
        and "secure" in cookie
        and "samesite=lax" in cookie
    )
    assert "path=/api/v1/auth" in cookie and "max-age=600" in cookie
    done = await web.client.get(
        web.fake.authorize(start.headers["location"], email()).removeprefix(ORIGIN)
    )
    cleared = [
        c for c in done.headers.get_list("set-cookie") if c.lower().startswith("aop_oidc_state=")
    ]
    assert cleared and "max-age=0" in cleared[0].lower()


@pytest.mark.parametrize(
    "bad",
    [
        {"nonce": "someone-elses"},
        {"aud": "another-client"},
        {"iss": "https://evil.test"},
        {"exp": 1},
        {"_sign_with_other": True},  # right kid, wrong key
        {"_alg": "none"},
        {"azp": "another-client"},
        {"aud": ["client-1", "another-client"]},  # several audiences and no azp
    ],
    ids=[
        "nonce",
        "aud",
        "iss",
        "expired",
        "forged-signature",
        "alg-none",
        "azp",
        "multi-aud-no-azp",
    ],
)
async def test_a_bad_id_token_refused(web, owner_engine, bad):
    addr = email()
    web.fake.override = bad
    r = await web.login(addr)
    assert r.status_code == 400 and r.json()["error"]["code"] == "login_failed"
    assert await fetch(owner_engine, "select 1 from users where email=:e", e=addr) == []


async def test_every_login_failure_has_the_same_body(web):
    bodies = set()
    for bad in ({"nonce": "x"}, {"aud": "x"}, {"exp": 1}, {"email_verified": False}):
        web.fake.override = bad
        r = await web.login(email())
        bodies.add(r.text)
    bodies.add((await web.client.get("/api/v1/auth/callback?code=a&state=b")).text)
    assert len(bodies) == 1  # catches: telling an attacker which check failed


async def test_session_id_regenerated_on_login(web, owner_engine):
    addr = email()
    await web.login(addr)
    old = web.client.cookies.get("aop_session")
    await web.login(addr)  # logging in again while holding a session
    new = web.client.cookies.get("aop_session")
    assert old and new and old != new  # catches: session fixation (a pre-set id surviving a login)
    stale = await web.client.get("/api/v1/auth/session", headers={"Cookie": f"aop_session={old}"})
    assert stale.status_code == 401
    assert (
        len(
            await fetch(
                owner_engine,
                "select 1 from web_sessions where user_id=:u",
                u=await user_id(owner_engine, addr),
            )
        )
        == 1
    )


async def test_a_session_id_chosen_by_the_attacker_is_never_adopted(web, owner_engine):
    addr = email()
    web.client.cookies.set(
        "aop_session", "attacker-chosen-value-0123456789abcdef0123456789a", domain="app.test"
    )
    r = await web.login(addr)
    assert r.status_code == 302
    assert (
        web.client.cookies.get("aop_session") != "attacker-chosen-value-0123456789abcdef0123456789a"
    )


async def test_session_stored_hashed(web, owner_engine):
    addr = email()
    await web.login(addr)
    raw = web.client.cookies.get("aop_session")
    rows = await fetch(
        owner_engine,
        "select id_hash from web_sessions where user_id=:u",
        u=await user_id(owner_engine, addr),
    )
    assert rows == [
        (hashlib.sha256(raw.encode()).hexdigest(),)
    ]  # catches: the raw session id in the database
    everything = "\n".join(
        r[0] for r in await fetch(owner_engine, "select row_to_json(x)::text from web_sessions x")
    )
    assert raw not in everything


async def test_cookie_flags_httponly_secure_samesite_lax(web):
    r = await web.login(email())
    cookie = r.headers["set-cookie"].lower()
    # catches: a weak cookie
    assert (
        "aop_session=" in cookie
        and "httponly" in cookie
        and "secure" in cookie
        and "samesite=lax" in cookie
        and "path=/" in cookie
    )
    assert "max-age=2592000" in cookie  # 30 days: the absolute cap


async def test_logout_deletes_row_and_old_cookie_401(web, owner_engine):
    addr = email()
    await web.login(addr)
    raw = web.client.cookies.get("aop_session")
    out = await web.post("/api/v1/auth/logout")
    assert out.status_code == 204
    assert (
        await fetch(
            owner_engine,
            "select 1 from web_sessions where user_id=:u",
            u=await user_id(owner_engine, addr),
        )
        == []
    )
    old = await web.client.get("/api/v1/auth/session", headers={"Cookie": f"aop_session={raw}"})
    assert old.status_code == 401  # catches: logout only clearing the cookie


async def test_idle_12h_expires_and_activity_slides_the_window(web):
    await web.login(email())
    web.clock.at += timedelta(hours=11)
    assert (await session_info(web)).status_code == 200  # slides the idle window
    web.clock.at += timedelta(hours=11, minutes=59)
    assert (await session_info(web)).status_code == 200  # 11h59m after the last request
    web.clock.at += timedelta(hours=12, seconds=1)
    assert (await session_info(web)).status_code == 401  # idle for more than 12h


async def test_the_absolute_30d_cap_ends_a_session_that_never_idles(web):
    await web.login(email())
    start = web.clock.at
    while web.clock.at + timedelta(hours=11) < start + timedelta(days=30) - timedelta(hours=2):
        web.clock.at += timedelta(hours=11)
        assert (
            await session_info(web)
        ).status_code == 200  # always active: the idle window never lapses
    web.clock.at = start + timedelta(days=30) - timedelta(hours=1)
    assert (await session_info(web)).status_code == 200  # still inside the cap
    web.clock.at += timedelta(hours=2)  # 30d + 1h: active 2h ago, so only the cap can end it
    assert (await session_info(web)).status_code == 401  # catches: a sliding window with no cap


async def test_an_expired_session_row_is_deleted(web, owner_engine):
    addr = email()
    await web.login(addr)
    web.clock.at += timedelta(hours=13)
    assert (await session_info(web)).status_code == 401
    assert (
        await fetch(
            owner_engine,
            "select 1 from web_sessions where user_id=:u",
            u=await user_id(owner_engine, addr),
        )
        == []
    )


async def test_selecting_an_organization_needs_a_membership(web, owner_engine):
    addr = email()
    await web.login(addr)
    mine, foreign = await make_org(owner_engine), await make_org(owner_engine)
    await make_member(owner_engine, mine, await user_id(owner_engine, addr), "manager")
    no = await web.post("/api/v1/auth/organization", json={"organization_id": str(foreign)})
    assert no.status_code == 404  # not a member: indistinguishable from "no such organization"
    assert (await session_info(web)).json()["organization_id"] is None
    ok = await web.post("/api/v1/auth/organization", json={"organization_id": str(mine)})
    assert ok.status_code == 200 and ok.json() == {
        "user_id": str(await user_id(owner_engine, addr)),
        "organization_id": str(mine),
        "role": "manager",
    }
    for bad in ("not-a-uuid", None, 5):
        assert (
            await web.post("/api/v1/auth/organization", json={"organization_id": bad})
        ).status_code == 422


async def test_removed_membership_loses_access_on_next_request(web, owner_engine):
    addr = email()
    await web.login(addr)
    org = await make_org(owner_engine)
    uid = await user_id(owner_engine, addr)
    await make_member(owner_engine, org, uid, "org_admin")
    await web.post("/api/v1/auth/organization", json={"organization_id": str(org)})
    assert (await session_info(web)).json()["role"] == "org_admin"
    async with owner_engine.begin() as c:
        await c.execute(
            text("delete from memberships where organization_id=:o and user_id=:u"),
            {"o": org, "u": uid},
        )
    after = (await session_info(web)).json()
    assert (
        after["organization_id"] is None and after["role"] is None
    )  # catches: the org id trusted from the session


async def test_role_changes_apply_on_the_next_request_and_a_switch_leaks_nothing_of_the_old_org(
    web, owner_engine
):
    addr = email()
    await web.login(addr)
    a, b = await make_org(owner_engine), await make_org(owner_engine)
    uid = await user_id(owner_engine, addr)
    await make_member(owner_engine, a, uid, "org_admin")
    await make_member(owner_engine, b, uid, "member")
    await web.post("/api/v1/auth/organization", json={"organization_id": str(a)})
    assert (await session_info(web)).json()["role"] == "org_admin"
    async with owner_engine.begin() as c:
        await c.execute(
            text("update memberships set role='member' where organization_id=:o and user_id=:u"),
            {"o": a, "u": uid},
        )
    assert (await session_info(web)).json()["role"] == "member"  # read fresh each request
    await web.post("/api/v1/auth/organization", json={"organization_id": str(b)})
    now = (await session_info(web)).json()
    assert (
        now["organization_id"] == str(b) and now["role"] == "member"
    )  # catches: the previous org's context surviving a switch


async def test_login_is_unavailable_when_oidc_is_not_configured(web, monkeypatch):
    monkeypatch.setattr(settings, "oidc_client_id", "")
    r = await web.client.get("/api/v1/auth/login")
    assert r.status_code == 503 and r.json()["error"]["code"] == "login_unavailable"


async def test_a_broken_issuer_discovery_is_a_clean_503_not_a_500(web):
    from httpx2 import MockTransport, Response

    from app.api import deps
    from app.main import app

    web.fake.transport()  # keep the real fake for the next case
    app.dependency_overrides[deps.get_oidc_transport] = lambda: MockTransport(
        lambda request: Response(
            200,
            json={
                "issuer": "https://someone-else.test",
                "authorization_endpoint": "x",
                "token_endpoint": "y",
                "jwks_uri": "z",
            },
        )
    )
    wrong_issuer = await web.client.get(
        "/api/v1/auth/login"
    )  # catches: trusting a discovery document of another issuer
    assert (
        wrong_issuer.status_code == 503
        and wrong_issuer.json()["error"]["code"] == "login_unavailable"
    )
    app.dependency_overrides[deps.get_oidc_transport] = lambda: MockTransport(
        lambda request: Response(500)
    )
    down = await web.client.get("/api/v1/auth/login")
    assert down.status_code == 503


async def test_an_id_token_for_several_audiences_with_our_azp_is_accepted(web):
    web.fake.override = {"aud": ["client-1", "another-client"], "azp": "client-1"}
    assert (await web.login(email())).status_code == 302


async def test_login_is_a_clean_503_when_redis_is_down(web):
    from app.api import deps
    from app.main import app

    class DownRedis:
        async def set(self, *a, **k):
            raise ConnectionError("redis is away")

    app.dependency_overrides[deps.get_redis] = lambda: DownRedis()
    r = await web.client.get("/api/v1/auth/login")  # catches: a 500 for a dependency outage
    assert r.status_code == 503 and r.json()["error"]["code"] == "login_unavailable"
    assert "set-cookie" not in r.headers


async def keep_active_until_just_before_the_cap(web):
    """Log in, then touch the session every 11h so the idle window never lapses; returns the login time."""
    await web.login(email())
    start = web.clock.at
    t = start
    while t + timedelta(hours=11) < start + timedelta(days=30):
        t += timedelta(hours=11)
        web.clock.at = t
        assert (await session_info(web)).status_code == 200
    return start


async def test_one_microsecond_inside_the_absolute_cap_is_alive(web):
    start = await keep_active_until_just_before_the_cap(web)
    web.clock.at = start + timedelta(days=30) - timedelta(microseconds=1)
    assert (await session_info(web)).status_code == 200


async def test_exactly_at_the_absolute_cap_is_expired(web):
    start = await keep_active_until_just_before_the_cap(web)
    web.clock.at = start + timedelta(days=30)
    assert (await session_info(web)).status_code == 401  # catches: `>` instead of `>=` at the cap

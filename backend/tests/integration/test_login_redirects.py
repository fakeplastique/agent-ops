from urllib.parse import parse_qs, urlparse

import pytest

from .test_login import bind_browser, email
from .web_harness import ORIGIN

pytestmark = pytest.mark.anyio


async def login_with(web, next_path: str | None = None, **claims):
    url = "/api/v1/auth/login" + (f"?next={next_path}" if next_path is not None else "")
    start = await web.client.get(url)
    assert start.status_code == 302
    callback = web.fake.authorize(start.headers["location"], email(), **claims)
    return await web.client.get(callback.removeprefix(ORIGIN))


async def test_success_lands_on_inbox_by_default(web):
    r = await login_with(web)
    assert r.status_code == 302 and r.headers["location"] == ORIGIN + "/inbox"


@pytest.mark.parametrize("path", ["/decisions/42?tab=votes", "/invite/abc_DEF-1"])
async def test_a_same_origin_next_is_honoured(web, path):
    r = await login_with(web, path)
    assert r.headers["location"] == ORIGIN + path


@pytest.mark.parametrize(
    "bad",
    [
        "//evil.test",
        "/%5Cevil.test",
        "https://evil.test/",
        "evil",
        "/a%0d%0ab",
        "/a%09b",
        "/a%5Cb",
        "",
    ],
)
async def test_an_unsafe_next_lands_on_inbox(web, bad):
    # catches: an open redirect through the login flow (same rule as the web's safeNext)
    r = await login_with(web, bad)
    assert r.headers["location"] == ORIGIN + "/inbox"


async def test_a_failed_callback_redirects_to_login_with_the_code(web):
    r = await login_with(web, email_verified=False)
    assert r.status_code == 302 and r.headers["location"] == f"{ORIGIN}/login?error=login_failed"
    assert "set-cookie" not in r.headers


async def test_every_failure_redirects_the_same_way(web):
    bad = await web.client.get("/api/v1/auth/callback?code=a&state=b")
    assert bad.headers["location"] == f"{ORIGIN}/login?error=login_failed"
    bare = await web.client.get("/api/v1/auth/callback")
    assert bare.headers["location"] == f"{ORIGIN}/login?error=login_failed"


async def test_google_access_denied_passes_through_and_nothing_else_does(web):
    start = await web.client.get("/api/v1/auth/login")
    state = parse_qs(urlparse(start.headers["location"]).query)["state"][0]
    bind_browser(web, state)
    denied = await web.client.get(f"/api/v1/auth/callback?error=access_denied&state={state}")
    assert denied.headers["location"] == f"{ORIGIN}/login?error=access_denied"
    # catches: an attacker-chosen error text reaching the login page
    other = await web.client.get(f"/api/v1/auth/callback?error=%3Cscript%3E&state={state}")
    assert other.headers["location"] == f"{ORIGIN}/login?error=login_failed"

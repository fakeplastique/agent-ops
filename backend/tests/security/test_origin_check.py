import httpx
import pytest
from fastapi import Depends, FastAPI

from app.authz.origin import require_same_origin
from app.config import settings
from tests.integration.web_harness import ORIGIN

pytestmark = pytest.mark.anyio

METHODS = ("POST", "PUT", "PATCH", "DELETE")


def probe_app() -> FastAPI:
    app = FastAPI()

    @app.api_route(
        "/probe",
        methods=[*METHODS, "GET", "HEAD", "OPTIONS"],
        dependencies=[Depends(require_same_origin)],
    )
    async def probe() -> dict[str, str]:
        return {"ok": "yes"}

    from app.api.errors import register_error_handlers

    register_error_handlers(app)
    return app


@pytest.fixture
async def client(monkeypatch):
    monkeypatch.setattr(settings, "web_origin", ORIGIN)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=probe_app()), base_url=ORIGIN
    ) as c:
        yield c


@pytest.mark.parametrize("method", METHODS)
@pytest.mark.parametrize(
    "origin",
    [
        None,
        "",
        "null",
        "https://evil.test",
        "http://app.test",
        "https://app.test.evil.test",
        "https://app.test:8443",
        "https://app.test/",
        "HTTPS://APP.TEST",
        "https://app.test, https://evil.test",
    ],
)
async def test_write_with_foreign_or_missing_origin_403(client, method, origin):
    headers = {} if origin is None else {"Origin": origin}
    r = await client.request(method, "/probe", headers=headers)
    assert (
        r.status_code == 403 and r.json()["error"]["code"] == "forbidden"
    )  # catches: a cookie-authenticated cross-site write


@pytest.mark.parametrize("method", METHODS)
async def test_write_with_the_web_origin_passes(client, method):
    assert (await client.request(method, "/probe", headers={"Origin": ORIGIN})).status_code == 200


@pytest.mark.parametrize("method", ["GET", "HEAD", "OPTIONS"])
async def test_safe_methods_need_no_origin(client, method):
    assert (await client.request(method, "/probe")).status_code == 200


async def test_the_cookie_routes_of_the_auth_router_are_covered(web):
    for path in ("/api/v1/auth/logout", "/api/v1/auth/organization"):
        for origin in (None, "https://evil.test"):
            r = await web.post(
                path,
                origin=origin,
                json={"organization_id": "00000000-0000-0000-0000-000000000000"},
            )
            assert r.status_code == 403, (path, origin)  # before the session is even looked at


def _depends_on(dependant, target) -> bool:
    return any(d.call is target or _depends_on(d, target) for d in dependant.dependencies)


# Routes that authenticate with a bearer token only (no ambient cookie): none yet. A new unsafe route is checked
# by default; listing it here is a decision a reviewer sees.
BEARER_ONLY: set[tuple[str, str]] = set()


def test_every_unsafe_route_is_origin_checked_or_declared_bearer_only():
    from app.main import app
    from tests.cross_tenant.harness import api_routes

    unsafe = [
        (m, path, r)
        for path, r in api_routes(app)
        for m in (r.methods or ())
        if m not in {"GET", "HEAD", "OPTIONS"}
    ]
    assert unsafe, "the route walk found no unsafe route: the guard would pass on nothing"
    missing = [
        (m, path)
        for m, path, r in unsafe
        if (m, path) not in BEARER_ONLY and not _depends_on(r.dependant, require_same_origin)
    ]
    assert missing == []  # catches: a cookie-authenticated write without the Origin check (FM36)

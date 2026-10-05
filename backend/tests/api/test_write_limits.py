import pytest

from app.api.deps import public_limit, user_write_limit, write_limit
from app.config import settings
from tests.cross_tenant.harness import api_routes

pytestmark = pytest.mark.anyio

UNLIMITED = {
    "/api/v1/auth/logout",
    "/api/v1/auth/organization",
}  # session plumbing, not tenant writes


def _depends_on(dependant, target) -> bool:
    return any(d.call is target or _depends_on(d, target) for d in dependant.dependencies)


def test_every_write_route_is_rate_limited():
    from app.main import app

    unsafe = [
        (m, path, r)
        for path, r in api_routes(app)
        for m in (r.methods or ())
        if m not in {"GET", "HEAD", "OPTIONS"} and path not in UNLIMITED
    ]
    assert len(unsafe) >= 4, "the walk found no write route: the guard would pass on nothing"
    # catches: a new write route that forgot the limiter
    missing = [
        (m, p)
        for m, p, r in unsafe
        if not any(
            _depends_on(r.dependant, lim) for lim in (write_limit, user_write_limit, public_limit)
        )
    ]
    assert missing == []


async def test_org_creation_by_one_user_is_limited(web, monkeypatch):
    monkeypatch.setattr(settings, "write_limit_per_principal", 2)
    await web.login("spammer@example.test")
    codes = [
        (
            await web.post("/api/v1/organizations", json={"name": "N", "slug": f"spam-org-{i}"})
        ).status_code
        for i in range(3)
    ]
    assert codes == [201, 201, 429]

import uuid
from datetime import timedelta

import httpx
import pytest
from fastapi import Depends, FastAPI
from redis.asyncio import Redis

from app.api import deps
from app.api.errors import register_error_handlers
from app.api.ratelimit import check_write
from app.authz.principals import AgentPrincipal
from app.config import settings
from app.domain.errors import RateLimited, RateLimiterUnavailable

from .helpers import FakeClock, utc

pytestmark = pytest.mark.anyio


@pytest.fixture
async def redis():
    r = Redis.from_url("redis://localhost:6379/15")
    await r.flushdb()
    yield r
    await r.flushdb()
    await r.aclose()


def agent(org: uuid.UUID, aid: uuid.UUID | None = None) -> AgentPrincipal:
    return AgentPrincipal(agent_id=aid or uuid.uuid4(), organization_id=org)


async def test_org_a_exhausting_its_limit_does_not_throttle_org_b(redis):
    clock, a, b = FakeClock(utc(2026, 1, 1)), uuid.uuid4(), uuid.uuid4()
    # catches: a global bucket
    for _ in range(3):
        await check_write(redis, agent(a), clock, per_principal=100, per_org=3)
    with pytest.raises(RateLimited):
        await check_write(redis, agent(a), clock, per_principal=100, per_org=3)
    await check_write(redis, agent(b), clock, per_principal=100, per_org=3)


async def test_limit_keys_include_org_and_principal(redis):
    clock, a, b, same = FakeClock(utc(2026, 1, 1)), uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    # catches: key collision across tenants
    await check_write(redis, agent(a, same), clock, per_principal=1, per_org=100)
    with pytest.raises(RateLimited):
        await check_write(redis, agent(a, same), clock, per_principal=1, per_org=100)
    await check_write(
        redis, agent(a), clock, per_principal=1, per_org=100
    )  # a peer in the same org
    await check_write(
        redis, agent(b, same), clock, per_principal=1, per_org=100
    )  # same id, other org


async def test_window_rolls_over_and_retry_after_counts_down(redis):
    clock, org = FakeClock(utc(2026, 1, 1, 0, 0, 20)), uuid.uuid4()
    await check_write(redis, agent(org), clock, per_principal=100, per_org=2)
    await check_write(redis, agent(org), clock, per_principal=100, per_org=2)
    with pytest.raises(RateLimited) as e:
        await check_write(redis, agent(org), clock, per_principal=100, per_org=2)
    assert e.value.retry_after == 40
    clock._now += timedelta(seconds=40)
    await check_write(redis, agent(org), clock, per_principal=100, per_org=2)


def app_with_write_route(r: Redis, clock: FakeClock, principal: AgentPrincipal) -> FastAPI:
    app = FastAPI()
    register_error_handlers(app)

    @app.post("/w", dependencies=[Depends(deps.write_limit)])
    async def w() -> dict:
        return {}

    app.dependency_overrides[deps.get_redis] = lambda: r
    app.dependency_overrides[deps.get_clock] = lambda: clock
    app.dependency_overrides[deps.get_current_principal] = lambda: principal
    return app


async def test_429_carries_retry_after_header_and_envelope(redis, monkeypatch):
    monkeypatch.setattr(settings, "write_limit_per_principal", 1)
    clock = FakeClock(utc(2026, 1, 1, 0, 0, 50))
    app = app_with_write_route(redis, clock, agent(uuid.uuid4()))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
        assert (await c.post("/w")).status_code == 200
        res = await c.post("/w")
    assert res.status_code == 429
    assert res.headers["retry-after"] == "10"
    assert res.json()["error"]["code"] == "rate_limited"


async def test_failed_auth_limited_per_ip(web, monkeypatch):
    monkeypatch.setattr(settings, "auth_failures_per_minute", 3)
    bad = {"Authorization": "Bearer pat_" + "x" * 43}
    # catches: brute force on tokens
    for _ in range(3):
        assert (await web.client.get("/api/v1/me", headers=bad)).status_code == 401
    res = await web.client.get("/api/v1/me", headers=bad)
    assert res.status_code == 429
    assert int(res.headers["retry-after"]) >= 1


async def test_redis_down_fails_closed_for_writes_with_503():
    dead = Redis.from_url("redis://localhost:1/0", socket_connect_timeout=0.2)
    # catches: limiter disabled by an outage
    with pytest.raises(RateLimiterUnavailable):
        await check_write(dead, agent(uuid.uuid4()), FakeClock(utc(2026, 1, 1)))
    app = app_with_write_route(dead, FakeClock(utc(2026, 1, 1)), agent(uuid.uuid4()))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
        res = await c.post("/w")
    assert res.status_code == 503
    assert res.json()["error"]["code"] == "rate_limiter_unavailable"
    await dead.aclose()


async def test_redis_down_does_not_lock_everyone_out_of_login_checks(app_engine, monkeypatch):
    # failed-auth throttling is best effort: tokens are 256-bit, so an outage must not take reads down
    from app.api.ratelimit import auth_blocked

    dead = Redis.from_url("redis://localhost:1/0", socket_connect_timeout=0.2)
    await auth_blocked(dead, "1.2.3.4", FakeClock(utc(2026, 1, 1)))
    await dead.aclose()


async def test_valid_token_still_works_while_its_ip_is_throttled(
    web, app_engine, owner_engine, monkeypatch
):
    from app.db.session import tenant_session
    from app.services.tokens import issue_agent_token

    from .helpers import make_world

    w = await make_world(owner_engine)
    async with tenant_session(app_engine, w.org) as s:
        token = await issue_agent_token(s, w.org, w.identity)
    monkeypatch.setattr(settings, "auth_failures_per_minute", 2)
    bad = {"Authorization": "Bearer pat_" + "x" * 43}
    for _ in range(3):
        await web.client.get("/api/v1/me", headers=bad)
    # catches: one attacker behind a shared proxy IP locking out every valid caller
    good = await web.client.get("/api/v1/me", headers={"Authorization": f"Bearer {token}"})
    assert good.status_code == 200

"""S26 / I15: cached flags and policy resolution, invalidated on every write, keyed by tenant, failing open."""

import asyncio
import uuid

import pytest
from sqlalchemy import text

from app.adapters.cache_redis import RedisCache
from app.config import settings
from app.db.session import tenant_session
from app.mcp import gating as mcp_gating
from app.mcp.server import mcp as server
from app.services import cache, invitations, members, plans, policies, teams
from app.services.features import is_enabled
from tests.api.harness import user_auth
from tests.mcp.conftest import mcp  # noqa: F401 (the fixture)

from .helpers import FakeClock, make_org, make_policy, make_team, staff, utc

pytestmark = pytest.mark.anyio

URL = "redis://localhost:6379/15"
PLAN = "/api/v1/organizations/current/plan"
LEVELS = [{"role": "manager", "timeout_minutes": 60}]
POLICY = {"name": "default", "quorum_size": 3, "require_distinct_agents": True, "rounds": 1,
          "confidence_threshold": 0.8, "levels": LEVELS, "voting_timeout_minutes": 60,
          "risk_floors": {}}  # fmt: skip


@pytest.fixture
async def cached():
    """The real Redis cache on db 15 for this test only; other tests keep the default (no cache)."""
    store = RedisCache(URL)
    await store.redis.flushdb()
    cache.configure(store)
    for c in (cache.HITS, cache.MISSES, cache.ERRORS):
        c.clear()
    yield store
    cache.configure(None)
    cache.BYPASS.clear()
    await store.redis.flushdb()
    await store.redis.aclose()


async def _flag(app_engine, org, flag="analytics") -> bool:
    async with tenant_session(app_engine, org) as s:
        return await is_enabled(s, org, flag)


async def _resolve(app_engine, org, user) -> dict:
    async with tenant_session(app_engine, org) as s:
        return await policies.resolve_policy(s, org, user)


async def test_key_includes_organization_id(cached, app_engine, owner_engine):
    # Two orgs, one flag key, one policy name: each reads its own answer, never the other's warm entry.
    a, b = await make_org(owner_engine, "pro"), await make_org(owner_engine, "free")
    ua, ub = await staff(owner_engine, a, "member"), await staff(owner_engine, b, "member")
    pa = await make_policy(owner_engine, a, quorum=5)
    pb = await make_policy(owner_engine, b, quorum=2)
    async with owner_engine.begin() as c:  # the same name in both tenants
        await c.execute(text("update escalation_policies set name='shared' where id in (:a, :b)"),
                        {"a": pa, "b": pb})  # fmt: skip
    assert await _flag(app_engine, a) is True
    assert await _flag(app_engine, b) is False  # catches: cross-tenant cache bleed
    assert (await _resolve(app_engine, a, ua))["policy_id"] == str(pa)
    assert (await _resolve(app_engine, b, ub))["policy_id"] == str(pb)
    keys = await cached.redis.keys("cache:*")
    assert keys and all(k.startswith((f"cache:{a}:", f"cache:{b}:")) for k in keys), keys
    entry = next(k for k in keys if k.endswith(":flags:analytics") and str(a) in k)
    assert 0 < await cached.redis.ttl(entry) <= cache.TTL == 60  # catches: entries without a TTL


async def test_policy_edit_override_plan_change_membership_change_each_invalidate(
    cached, app_engine, owner_engine
):
    org = await make_org(owner_engine, "enterprise")
    admin = await staff(owner_engine, org, "org_admin")
    parent, child = await make_team(owner_engine, org), await make_team(owner_engine, org)
    user = await staff(owner_engine, org, "member", team=child)
    default = await make_policy(owner_engine, org, quorum=3)
    team_policy = await make_policy(owner_engine, org, parent, quorum=4)

    async def resolved() -> dict:
        first = await _resolve(app_engine, org, user)
        assert await _resolve(app_engine, org, user) == first  # the second read is a hit
        return first

    assert (await resolved())["policy_id"] == str(default)
    async with tenant_session(app_engine, org) as s:  # team move: child now sits under parent
        await teams.update_team(s, org, admin, child, {"parent_team_id": parent})
    assert (await resolved())["policy_id"] == str(team_policy)
    async with tenant_session(app_engine, org) as s:  # policy edit
        await policies.update_policy(s, org, admin, team_policy, {"quorum_size": 5})
    assert (await resolved())["quorum_size"] == 5
    async with tenant_session(app_engine, org) as s:  # policy delete
        await policies.delete_policy(s, org, admin, team_policy)
    assert (await resolved())["policy_id"] == str(default)
    async with tenant_session(app_engine, org) as s:  # policy create
        made = await policies.create_policy(s, org, admin, child, POLICY | {"quorum_size": 2})
    assert (await resolved())["policy_id"] == str(made["id"])
    async with tenant_session(app_engine, org) as s:
        await policies.delete_policy(s, org, admin, made["id"])
    hit = await resolved()
    async with tenant_session(app_engine, org) as s:  # membership: removed, so no team any more
        await members.remove_member(s, org, user, admin)
    after = await resolved()
    assert hit["owner_team_id"] == str(child) and after["owner_team_id"] is None
    email = f"back-{uuid.uuid4()}@example.test"
    async with owner_engine.begin() as c:
        await c.execute(text("update users set email=:e where id=:u"), {"e": email, "u": user})
    clock = FakeClock(utc(2026, 1, 1))
    async with tenant_session(app_engine, org) as s:  # membership: rejoins child by invitation
        _, raw = await invitations.create_invitation(s, org, admin, email, "member", child, clock)
    await invitations.accept_invitation(app_engine, user, raw, clock)
    assert (await resolved())["owner_team_id"] == str(child)
    async with tenant_session(app_engine, org) as s:  # team move back out
        await teams.update_team(s, org, admin, child, {"parent_team_id": None})
    assert (await resolved())["policy_id"] == str(default)

    # Flags: a plan change invalidates through the service.
    assert await _flag(app_engine, org, "search") is True
    async with tenant_session(app_engine, org) as s:
        await plans.change_plan(s, org, "pro", admin, "platform")
    assert await _flag(app_engine, org, "search") is False
    # Overrides have no app write path (app_rt holds no grant); an operator's SQL edit is followed by
    # cache.invalidate_now (D27), else it shows within the 60 s TTL.
    async with owner_engine.begin() as c:
        await c.execute(
            text("insert into organization_feature_overrides values (:o, 'search', true)"),
            {"o": org},
        )
    await cache.invalidate_now(org)
    assert await _flag(app_engine, org, "search") is True


async def test_plan_downgrade_visible_on_next_request_despite_warm_cache(
    cached, web, app_engine, owner_engine, monkeypatch
):
    org = await make_org(owner_engine, "pro")
    admin = await user_auth(app_engine, org, await staff(owner_engine, org, "org_admin"))
    for _ in range(2):  # warm, then served from the cache
        got = (await web.client.get(PLAN, headers=admin)).json()
        assert "analytics" in got["flags"]
    assert cache.HITS["flags"] >= 1
    monkeypatch.setattr(settings, "demo_mode", True)
    r = await web.client.put(PLAN, json={"plan": "free"}, headers=admin)
    # The writer's own response reads past its warm entries (they are stale inside its transaction).
    assert r.status_code == 200 and r.json()["flags"] == [], r.text
    got = (await web.client.get(PLAN, headers=admin)).json()
    assert got["plan"] == "free" and got["flags"] == []  # catches: Pro flags served for 60 s


async def test_mcp_gate_sees_demo_switch_despite_warm_cache(
    cached,
    web,
    mcp,  # noqa: F811 (the imported fixture)
    app_engine,
    owner_engine,
    monkeypatch,
):
    # SC22 on the MCP path: the tool gate and the REST switch share one cache and one invalidation.
    @mcp_gating.gated("analytics")
    async def cache_probe_tool() -> str:
        """Test-only."""
        return "ran"

    server.add_tool(cache_probe_tool)
    try:
        org = await make_org(owner_engine, "free")
        admin = await user_auth(app_engine, org, await staff(owner_engine, org, "org_admin"))
        async with mcp.client(admin) as client:
            for _ in range(2):
                assert (await client.call_tool("cache_probe_tool", {})).is_error
            monkeypatch.setattr(settings, "demo_mode", True)
            r = await web.client.put(PLAN, json={"plan": "pro"}, headers=admin)
            assert r.status_code == 200, r.text
            r = await client.call_tool("cache_probe_tool", {})
            assert not r.is_error, r.content
    finally:
        server.remove_tool("cache_probe_tool")
        mcp_gating.REGISTRY.pop("cache_probe_tool")


async def test_hit_and_miss_counters_by_cache_name(
    cached, web, app_engine, owner_engine, monkeypatch
):
    org = await make_org(owner_engine, "pro")
    user = await staff(owner_engine, org, "member")
    await make_policy(owner_engine, org)
    for _ in range(3):
        await _flag(app_engine, org)
    for _ in range(2):
        await _resolve(app_engine, org, user)
    assert (cache.HITS["flags"], cache.MISSES["flags"]) == (2, 1)
    assert (cache.HITS["policy"], cache.MISSES["policy"]) == (1, 1)
    monkeypatch.setattr(settings, "metrics_token", "")
    assert (await web.client.get("/metrics")).status_code == 404  # off unless a token is set
    monkeypatch.setattr(settings, "metrics_token", "scrape-secret")
    assert (await web.client.get("/metrics")).status_code == 404
    bad = {"X-Metrics-Token": "nope"}
    assert (await web.client.get("/metrics", headers=bad)).status_code == 404
    r = await web.client.get("/metrics", headers={"X-Metrics-Token": "scrape-secret"})
    assert r.status_code == 200, r.text
    assert 'cache_hit_total{cache="flags"} 2' in r.text
    assert 'cache_miss_total{cache="flags"} 1' in r.text
    assert 'cache_hit_total{cache="policy"} 1' in r.text
    assert str(org) not in r.text and str(user) not in r.text  # counters only, no tenant ids


async def test_redis_down_falls_through_to_db_not_to_error(web, app_engine, owner_engine):
    dead = RedisCache("redis://127.0.0.1:1/0")  # nothing listens on port 1
    cache.configure(dead)
    try:
        org = await make_org(owner_engine, "pro")
        admin_id = await staff(owner_engine, org, "org_admin")
        user = await staff(owner_engine, org, "member")
        policy = await make_policy(owner_engine, org)
        assert await _flag(app_engine, org) is True
        assert (await _resolve(app_engine, org, user))["policy_id"] == str(policy)
        async with tenant_session(
            app_engine, org
        ) as s:  # the post-commit invalidation fails quietly
            await plans.change_plan(s, org, "free", admin_id, "platform")
        assert await _flag(app_engine, org) is False
        admin = await user_auth(app_engine, org, admin_id)
        r = await web.client.get(PLAN, headers=admin)
        assert r.status_code == 200 and r.json()["plan"] == "free", r.text
        assert cache.ERRORS["flags"] >= 1  # an outage is visible, not silent
    finally:
        cache.configure(None)
        cache.BYPASS.clear()
        await dead.redis.aclose()


async def test_failed_invalidation_bypasses_the_cache_for_that_org(
    cached, app_engine, owner_engine, monkeypatch
):
    # Redis answers reads but the post-commit stamp write fails: this process must not serve the warm entry.
    org, other = await make_org(owner_engine, "pro"), await make_org(owner_engine, "pro")
    admin = await staff(owner_engine, org, "org_admin")
    assert await _flag(app_engine, org) is True and await _flag(app_engine, other) is True

    async def broken(_org):
        raise ConnectionError("redis went away")

    monkeypatch.setattr(cached, "bump", broken)
    async with tenant_session(app_engine, org) as s:
        await plans.change_plan(s, org, "free", admin, "platform")
    assert await _flag(app_engine, org) is False
    assert (
        await _flag(app_engine, other) is True and cache.HITS["flags"] >= 1
    )  # others still cached


async def test_cache_write_racing_invalidation_does_not_resurrect_stale_data(cached):
    # A reader loads the old value, a writer commits and invalidates, then the reader stores what it loaded.
    org = uuid.uuid4()
    loading, release = asyncio.Event(), asyncio.Event()

    async def slow_old() -> str:
        loading.set()
        await release.wait()
        return "old"

    async def new() -> str:
        return "new"

    reader = asyncio.create_task(cache.get_or_load(None, org, "probe", "x", slow_old))
    await loading.wait()
    await cache.invalidate_now(org)  # the writer, after its commit
    release.set()
    assert await reader == "old"  # the reader's own answer predates the write: fine
    assert await cache.get_or_load(None, org, "probe", "x", new) == "new"  # never the late "old"


async def test_reader_during_the_write_transaction_cannot_pin_the_old_plan(
    cached, app_engine, owner_engine
):
    # A reader between the write and its commit sees the old plan and caches it: that entry must die at commit.
    org = await make_org(owner_engine, "pro")
    admin = await staff(owner_engine, org, "org_admin")
    async with tenant_session(app_engine, org) as s:
        await plans.change_plan(s, org, "free", admin, "platform")
        assert await _flag(app_engine, org) is True  # another transaction, before the commit
    assert await _flag(app_engine, org) is False  # catches: invalidating before the commit

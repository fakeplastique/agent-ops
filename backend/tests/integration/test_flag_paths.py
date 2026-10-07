"""FM28 / I15 / SC21 / SC22: every gated REST route and MCP tool answers the 402 contract on Free, and a plan
switch (demo or platform admin) is seen by the very next request."""

import re
import uuid

import httpx
import pytest
from fastapi import APIRouter, Depends, FastAPI
from redis.asyncio import Redis
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.api import deps, gating
from app.api.errors import register_error_handlers
from app.api.v1 import api_v1_router
from app.config import settings
from app.db.session import tenant_session
from app.main import app
from app.mcp import gating as mcp_gating
from app.mcp.server import mcp as server
from tests.api.harness import agent_auth, user_auth
from tests.cross_tenant.harness import api_routes
from tests.integration.web_harness import ORIGIN
from tests.mcp.conftest import mcp  # noqa: F401 (the fixture)

from .helpers import fetch, make_org, make_team, make_world, set_usage, staff

pytestmark = pytest.mark.anyio

# The spec 6 table, written out by hand: the oracle for details.required_plan.
CHEAPEST = {
    "attachments": "pro", "telegram": "pro", "analytics": "pro",
    "search": "enterprise", "team_policies": "enterprise", "audit_export": "enterprise",
}  # fmt: skip
PLAN = "/api/v1/organizations/current/plan"
EP = "/api/v1/escalation-policies"
POLICY = {
    "name": "default",
    "quorum_size": 3,
    "levels": [{"role": "manager", "timeout_minutes": 60}],
}


def _platform(org) -> str:
    return f"/api/v1/platform/organizations/{org}/plan"


def gated_routes(application) -> list[tuple[str, str, gating.Gate]]:
    """(method, path, gate) for every route that carries a gate anywhere in its dependency tree."""

    def gates(dependant):
        for d in dependant.dependencies:
            if isinstance(d.call, gating.Gate):
                yield d.call
            yield from gates(d)

    return [
        (m, path, g) for path, r in api_routes(application) for g in gates(r.dependant)
        for m in r.methods or ()
    ]  # fmt: skip


def gated_tools() -> dict[str, str]:
    """name -> flag for every tool on the server whose function carries the gate's mark."""
    tools = server._tool_manager.list_tools()
    return {t.name: g for t in tools if (g := vars(t.fn).get("__gate__"))}


@pytest.fixture
async def probed(app_engine):
    """The real v1 routes plus one probe route and one probe tool marked with the gates, removed afterwards.

    The probes prove the iteration runs on something even before a slice adds a real gated path."""
    probe_gate = gating.gate("analytics")
    router = APIRouter()

    @router.post("/api/v1/plan-gate-probe", dependencies=[Depends(probe_gate)])
    async def plan_gate_probe() -> dict[str, str]:
        return {"ok": "yes"}

    @mcp_gating.gated("search")
    async def plan_gate_probe_tool() -> str:
        """Test-only."""
        return "ran"

    probe = FastAPI()
    register_error_handlers(probe)
    probe.include_router(api_v1_router)
    probe.include_router(router)
    redis = Redis.from_url("redis://localhost:6379/15")
    await redis.flushdb()
    probe.dependency_overrides[deps.get_engine] = lambda: app_engine
    probe.dependency_overrides[deps.get_redis] = lambda: redis
    server.add_tool(plan_gate_probe_tool)
    try:
        yield probe, probe_gate
    finally:
        server.remove_tool("plan_gate_probe_tool")
        mcp_gating.REGISTRY.pop("plan_gate_probe_tool")
        gating.REGISTRY.remove(probe_gate)
        await redis.aclose()


async def _admin(owner_engine, app_engine, plan: str):
    org = await make_org(owner_engine, plan)
    return org, await user_auth(app_engine, org, await staff(owner_engine, org, "org_admin"))


async def test_registry_covers_all_routes_marked_with_the_gate_decorator(probed):
    probe, probe_gate = probed
    marked = {g for _, _, g in gated_routes(probe)}
    # catches: a gate created but never mounted, or mounted without being registered (both ways)
    assert marked == set(gating.REGISTRY)
    assert probe_gate in marked
    assert {g for _, _, g in gated_routes(app)} == set(gating.REGISTRY) - {probe_gate}
    assert ("POST", EP) in {(m, p) for m, p, _ in gated_routes(app)}  # team policies, today's gate
    spec = app.openapi()["paths"]
    for m, p, _ in gated_routes(app):
        assert "402" in spec[p][m.lower()]["responses"], (m, p)  # the contract is documented too
    assert gated_tools() == {n: g.flag for n, g in mcp_gating.REGISTRY.items()}
    assert "plan_gate_probe_tool" in gated_tools()


async def test_every_gated_route_and_tool_returns_402_with_required_plan_and_flag_on_free(
    probed,
    mcp,  # noqa: F811 (the imported fixture)
    app_engine,
    owner_engine,
):
    probe, _ = probed
    free, free_admin = await _admin(owner_engine, app_engine, "free")
    _, ent_admin = await _admin(owner_engine, app_engine, "enterprise")
    seen = 0
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=probe), base_url=ORIGIN) as c:
        for method, path, g in gated_routes(probe):
            assert g in gating.REGISTRY
            url = re.sub(r"{\w+}", lambda _: str(uuid.uuid4()), path)
            r = await c.request(method, url, json=g.probe, headers=free_admin)
            # catches: a gated path that answers anything but the 402 contract on Free (FM28)
            assert r.status_code == 402, (method, path, r.text)
            err = r.json()["error"]
            assert err["code"] == "plan_feature_required"
            assert err["details"] == {"flag": g.flag, "required_plan": CHEAPEST[g.flag]}
            ok = await c.request(method, url, json=g.probe, headers=ent_admin)
            assert ok.status_code != 402, (method, path, ok.text)  # the gate reads the plan
            seen += 1
    tools = gated_tools()
    for headers in (free_admin, await agent_auth(app_engine, *await _free_agent(owner_engine))):
        async with mcp.client(headers) as client:
            for name, flag in tools.items():
                r = await client.call_tool(name, mcp_gating.REGISTRY[name].probe)
                assert r.is_error, (name, r.structured_content)
                msg = r.content[0].text
                assert "plan feature required: the" in msg, msg
                assert f"the {CHEAPEST[flag]} plan is needed for {flag}" in msg, msg
                seen += 1
    async with mcp.client(ent_admin) as client:
        for name in tools:
            r = await client.call_tool(name, mcp_gating.REGISTRY[name].probe)
            assert not r.is_error, (name, r.content)
    assert seen >= 4, "the registry walk found nothing to probe: the guard would pass on nothing"
    assert free  # the Free org stays Free throughout


async def _free_agent(owner_engine):
    w = await make_world(owner_engine, "free")
    return w.org, w.identity


async def test_demo_switch_only_with_DEMO_MODE_and_only_for_own_org(
    web, app_engine, owner_engine, monkeypatch
):
    org, admin = await _admin(owner_engine, app_engine, "free")
    other = await make_org(owner_engine, "free")
    monkeypatch.setattr(settings, "demo_mode", False)
    r = await web.client.put(PLAN, json={"plan": "pro"}, headers=admin)
    # catches: an org admin switching plans in production
    assert r.status_code == 403 and r.json()["error"]["code"] == "forbidden", r.text
    monkeypatch.setattr(settings, "demo_mode", True)
    member = await user_auth(app_engine, org, await staff(owner_engine, org, "manager"))
    assert (await web.client.put(PLAN, json={"plan": "pro"}, headers=member)).status_code == 403
    w = await make_world(owner_engine, "free")
    agent = await agent_auth(app_engine, w.org, w.identity)
    assert (await web.client.put(PLAN, json={"plan": "pro"}, headers=agent)).status_code == 403
    r = await web.client.put(PLAN, json={"plan": "pro"}, headers=admin)
    assert r.status_code == 200 and r.json()["plan"] == "pro", r.text
    # Own org only: the demo switch never reaches another tenant, not even through the platform route.
    r = await web.client.put(_platform(other), json={"plan": "pro"}, headers=admin)
    assert r.status_code == 403, r.text
    plans = await fetch(owner_engine, "select id, plan_key from organizations where id in (:a, :b)",
                        a=org, b=other)  # fmt: skip
    assert dict(plans) == {org: "pro", other: "free"}
    assert (await web.client.put(PLAN, json={"plan": "gold"}, headers=admin)).status_code == 422
    # A browser session: the write needs the web app's Origin (get_actor checks it).
    email = f"demo-{uuid.uuid4()}@example.test"
    await web.login(email)
    user = (await fetch(owner_engine, "select id from users where email=:e", e=email))[0][0]
    third = await make_org(owner_engine, "free")
    async with owner_engine.begin() as c:
        await c.execute(
            text("insert into memberships (organization_id, user_id, role) values (:o, :u, 'org_admin')"),
            {"o": third, "u": user},
        )  # fmt: skip
    assert (await web.post("/api/v1/auth/organization", json={"organization_id": str(third)})).status_code == 200  # fmt: skip
    assert (await web.client.put(PLAN, json={"plan": "pro"})).status_code == 403  # no Origin
    r = await web.client.put(PLAN, json={"plan": "pro"}, headers={"Origin": ORIGIN})
    assert r.status_code == 200, r.text


async def test_platform_admin_only_from_env_allowlist(web, app_engine, owner_engine, monkeypatch):
    home, _ = await _admin(owner_engine, app_engine, "free")
    boss = await staff(owner_engine, home, "member")  # the platform role is not an org role
    boss_pat = await user_auth(app_engine, home, boss)
    (email,) = (await fetch(owner_engine, "select email from users where id=:u", u=boss))[0]
    target = await make_org(owner_engine, "free")
    body = {"plan": "enterprise"}
    monkeypatch.setattr(
        settings, "demo_mode", True
    )  # demo mode never makes anyone a platform admin
    for allow in ("", "someone@example.test", f"{email}x", ","):
        monkeypatch.setattr(settings, "platform_admin_emails", allow)
        r = await web.client.put(_platform(target), json=body, headers=boss_pat)
        assert r.status_code == 403, (allow, r.text)
    monkeypatch.setattr(settings, "platform_admin_emails", f"other@example.test, {email.upper()} ")
    r = await web.client.put(_platform(target), json=body, headers=boss_pat)
    assert r.status_code == 200 and r.json()["plan"] == "enterprise", r.text
    assert await fetch(
        owner_engine, "select plan_key from organizations where id=:o", o=target
    ) == [("enterprise",)]
    assert (await web.client.put(_platform(uuid.uuid4()), json=body, headers=boss_pat)).status_code == 404  # fmt: skip
    w = await make_world(owner_engine, "free")  # an agent has no email to be on the list
    agent = await agent_auth(app_engine, w.org, w.identity)
    assert (await web.client.put(_platform(target), json=body, headers=agent)).status_code == 403
    # catches: platform_admin settable through the API (the env list is the only source)
    spec = (await web.client.get("/api/v1/openapi.json")).json()
    assert "platform_admin" not in str(spec)


async def test_plan_function_refuses_another_tenant(app_engine, owner_engine):
    a, b = await make_org(owner_engine, "free"), await make_org(owner_engine, "free")
    # catches: the SECURITY DEFINER path used from tenant A's session to promote tenant B
    with pytest.raises(DBAPIError):
        async with tenant_session(app_engine, a) as s:
            await s.execute(text("select set_organization_plan(:o, 'enterprise')"), {"o": b})
    assert await fetch(owner_engine, "select plan_key from organizations where id=:o", o=b) == [
        ("free",)
    ]


async def test_switch_takes_effect_on_next_request_and_emits_plan_changed_and_audit(
    web, app_engine, owner_engine, monkeypatch
):
    org, admin = await _admin(owner_engine, app_engine, "free")
    await set_usage(owner_engine, org, "2026-01", 7)  # the web fixture's clock is 2026-01-15
    team = await make_team(owner_engine, org)
    team_policy = POLICY | {"team_id": str(team)}
    member = await user_auth(app_engine, org, await staff(owner_engine, org, "member"))
    got = (await web.client.get(PLAN, headers=member)).json()
    assert got["plan"] == "free" and got["flags"] == []
    assert got["usage"]["requests"] == {"used": 7, "limit": 50, "period": "2026-01"}
    assert got["limits"]["max_quorum"] == 3
    assert (await web.client.post(EP, json=team_policy, headers=admin)).status_code == 402
    home, _ = await _admin(owner_engine, app_engine, "free")
    boss = await staff(owner_engine, home, "org_admin")
    (email,) = (await fetch(owner_engine, "select email from users where id=:u", u=boss))[0]
    monkeypatch.setattr(settings, "platform_admin_emails", email)
    boss_pat = await user_auth(app_engine, home, boss)
    r = await web.client.put(_platform(org), json={"plan": "enterprise"}, headers=boss_pat)
    assert r.status_code == 200, r.text
    # catches: stale flags after a switch (the very next request sees Enterprise)
    assert (await web.client.post(EP, json=team_policy, headers=admin)).status_code == 201
    got = (await web.client.get(PLAN, headers=member)).json()
    assert got["plan"] == "enterprise" and "team_policies" in got["flags"]
    assert got["usage"]["requests"]["limit"] is None
    audit = await fetch(
        owner_engine,
        "select actor_type, actor_id, data from audit_logs where organization_id=:o"
        " and entity_type='organization' and action='plan_changed'",
        o=org,
    )
    assert audit == [("user", boss, {"from": "free", "to": "enterprise", "via": "platform"})]
    events = await fetch(
        owner_engine,
        "select payload from outbox_events where organization_id=:o and type='plan.changed'",
        o=org,
    )
    assert events == [({"organization_id": str(org), "from": "free", "to": "enterprise"},)]

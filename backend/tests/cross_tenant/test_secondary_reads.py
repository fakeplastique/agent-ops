"""FM4 / I1: secondary reads (list totals, nextLink, filters, stats, audit, plan usage) never show another tenant.

Every id-less GET route is walked from the app, so a new list or aggregate (events, search, attachments) is
probed here as soon as it exists; one that needs parameters fails until it gets a row in QUERIES."""

import pytest

from app.db.session import tenant_session
from app.main import app
from app.services.tokens import create_pat
from tests.api.harness import agent_auth
from tests.integration.helpers import (
    LEVELS3,
    cast,
    decide,
    make_world,
    open_request,
    pending_step,
    staff,
    voters,
)

from .harness import api_routes

pytestmark = pytest.mark.anyio

# Not tenant reads: login flow, health, process metrics, and the cookie session's own identity.
SKIP = {"/healthz", "/readyz", "/metrics", "/api/v1/auth/login", "/api/v1/auth/callback"}
SKIP |= {"/api/v1/auth/session", "/api/v1/me/organizations"}  # cookie session only


def queries(a: dict) -> dict[str, list[dict]]:
    """Extra query strings per path, some naming tenant A's ids as filters; every path also runs bare and
    with limit=1 (nextLink and total)."""
    return {
        "/api/v1/audit-logs": [{"decision_request_id": a["request"]}],
        "/api/v1/decision-requests": [
            {"agent_id": a["agent"]}, {"q": "Refund"}, {"status": "approved"},
            {"risk_level": "medium"},
        ],
        "/api/v1/stats/summary": [{"days": 366}],
        "/api/v1/stats/timeseries": [{"days": 366}],
    }  # fmt: skip


def probes(a: dict) -> list[tuple[str, dict]]:
    paths = sorted(
        p
        for p, r in api_routes(app)
        if "GET" in (r.methods or ()) and "{" not in p and p not in SKIP
    )
    assert (
        "/api/v1/stats/summary" in paths and "/api/v1/audit-logs" in paths
    )  # the walk sees routes
    extra = queries(a)
    return [(p, q) for p in paths for q in ({}, {"limit": 1}, *extra.get(p, []))]


async def _busy(owner, app_engine, w):
    """More of everything in tenant A: a request each way, votes, a human decision."""
    escalated = await open_request(app_engine, w)
    auto = await open_request(app_engine, w, action_type="note")
    for s in await voters(owner, w, 3):
        await cast(app_engine, w, escalated, s)
        await cast(app_engine, w, auto, s)
    step, *_ = await pending_step(owner, escalated)
    await decide(app_engine, w, step, await staff(owner, w.org, "org_admin"))
    return escalated


async def _read(web, principals, plan):
    out = {}
    for who, h in principals.items():
        for path, q in plan:
            r = await web.client.get(path, params=q, headers=h)
            out[(who, path, repr(q))] = (r.status_code, r.text)
    return out


async def test_secondary_reads_table(web, app_engine, owner_engine):
    a = await make_world(owner_engine, "enterprise", floors={"refund": "medium", "note": "low"},
                         levels=LEVELS3)  # fmt: skip
    first = await _busy(owner_engine, app_engine, a)
    ids = {"request": str(first["id"]), "agent": str(a.identity)}

    b = await make_world(owner_engine, "enterprise")  # same plan, every flag on, zero activity
    admin = await staff(owner_engine, b.org, "org_admin")
    async with tenant_session(app_engine, b.org) as s:
        _, raw = await create_pat(s, b.org, admin, "b")
    principals = {
        "b admin": {"Authorization": f"Bearer {raw}"},
        "b agent": await agent_auth(app_engine, b.org, b.identity),
    }
    plan = probes(ids)
    before = await _read(web, principals, plan)
    assert any(code == 200 for code, _ in before.values())

    await _busy(owner_engine, app_engine, a)  # A grows: B's lists, totals and stats must not move
    after = await _read(web, principals, plan)
    for key, (code, body) in after.items():
        # catches: a total, nextLink, count or stat of B's that moves with tenant A's activity
        assert (code, body) == before[key], key
        assert code in (200, 403), (
            key,
            body,
        )  # 403 = the role may not call it; 422 = missing row above
        for leak in (str(a.org), ids["request"], ids["agent"], str(a.owner_user), str(a.policy)):
            assert leak not in body, (key, leak)

import uuid

import pytest
from sqlalchemy import text

from app.db.session import tenant_session
from app.services.features import is_enabled

from .helpers import make_org

pytestmark = pytest.mark.anyio

PRO = {"attachments", "telegram", "analytics"}
# Literal copy of spec section 6 (None = unlimited / any).
SPEC = {
    "free": (50, 3, 1, 1, set()),
    "pro": (1000, 7, 2, 3, PRO),
    "enterprise": (None, None, 2, 3, PRO | {"search", "team_policies", "audit_export"}),
}


async def test_seed_matches_spec_table(app_engine):
    async with app_engine.connect() as c:
        plans = {
            r[0]: r[1:]
            for r in await c.execute(
                text(
                    "select key, requests_per_month, max_quorum, max_rounds, max_levels from plans"
                )
            )
        }
        flags: dict[str, set[str]] = {}
        for plan, flag in await c.execute(text("select plan_key, flag_key from plan_features")):
            flags.setdefault(plan, set()).add(flag)
    assert plans == {k: v[:4] for k, v in SPEC.items()}  # catches: seed drift from the spec
    assert {k: flags.get(k, set()) for k in SPEC} == {k: v[4] for k, v in SPEC.items()}


async def test_override_wins_over_plan_both_ways(app_engine, owner_engine):
    pro, free = await make_org(owner_engine, "pro"), await make_org(owner_engine, "free")
    async with owner_engine.begin() as c:
        await c.execute(
            text("insert into organization_feature_overrides values (:o, :f, :e)"),
            [
                {"o": pro, "f": "analytics", "e": False},  # revoke what the plan grants
                {"o": free, "f": "analytics", "e": True},  # grant what the plan lacks
            ],
        )
    async with tenant_session(app_engine, pro) as s:
        assert not await is_enabled(s, pro, "analytics")
        assert await is_enabled(s, pro, "telegram")
    async with tenant_session(app_engine, free) as s:
        assert await is_enabled(s, free, "analytics")


async def test_unknown_flag_is_disabled(app_engine, owner_engine):
    org = await make_org(owner_engine, "enterprise")
    async with tenant_session(app_engine, org) as s:
        assert not await is_enabled(s, org, "no_such_flag")  # catches: unknown key failing open


async def test_unknown_organization_is_disabled(app_engine):
    ghost = uuid.uuid4()
    async with tenant_session(app_engine, ghost) as s:
        assert not await is_enabled(s, ghost, "analytics")


async def test_cross_tenant_usage_overrides_and_organizations_hidden(app_engine, owner_engine):
    a, b = await make_org(owner_engine, "pro"), await make_org(owner_engine, "pro")
    async with owner_engine.begin() as c:
        await c.execute(
            text("insert into organization_feature_overrides values (:o, 'search', true)"), {"o": a}
        )
        await c.execute(
            text(
                "insert into usage_counters (organization_id, metric, period, value)"
                " values (:o, 'requests', '2026-01', 5)"
            ),
            {"o": a},
        )
    async with tenant_session(app_engine, b) as s:  # catches: tenant B reading tenant A's rows
        for table, col in (
            ("usage_counters", "organization_id"),
            ("organization_feature_overrides", "organization_id"),
            ("organizations", "id"),
        ):
            n = await s.scalar(text(f"select count(*) from {table} where {col} = :a"), {"a": a})
            assert n == 0, table

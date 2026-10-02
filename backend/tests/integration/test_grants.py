import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.db.session import tenant_session

from .helpers import make_org, set_usage

pytestmark = pytest.mark.anyio


async def _refused(engine, org, sql: str, **params) -> bool:
    try:
        async with tenant_session(engine, org) as s:
            await s.execute(text(sql), params)
    except DBAPIError:
        return True
    return False


async def test_runtime_role_cannot_change_its_own_plan_but_can_rename(app_engine, owner_engine):
    org = await make_org(owner_engine, "free")
    # catches: a tenant promoting itself by UPDATE (S25 adds a guarded path)
    assert await _refused(
        app_engine, org, "update organizations set plan_key='enterprise' where id=:o", o=org
    )
    assert not await _refused(
        app_engine, org, "update organizations set name='x' where id=:o", o=org
    )


async def test_runtime_role_cannot_grant_itself_a_flag(app_engine, owner_engine):
    org = await make_org(owner_engine, "free")
    sql = "insert into organization_feature_overrides values (:o, 'audit_export', true)"
    assert await _refused(app_engine, org, sql, o=org)  # catches: tenant-written overrides


async def test_runtime_role_cannot_edit_plan_reference_data(app_engine, owner_engine):
    org = await make_org(owner_engine, "free")
    assert await _refused(
        app_engine, org, "update plans set requests_per_month=NULL where key='free'"
    )
    assert await _refused(app_engine, org, "insert into plan_features values ('free', 'search')")


async def test_usage_counter_can_only_go_up(app_engine, owner_engine):
    org = await make_org(owner_engine, "free")
    await set_usage(owner_engine, org, "2026-01", 10)
    up = "update usage_counters set value = value + 1 where organization_id=:o"
    down = "update usage_counters set value = 0 where organization_id=:o"
    assert not await _refused(app_engine, org, up, o=org)
    assert await _refused(app_engine, org, down, o=org)  # catches: resetting the monthly counter
    assert await _refused(
        app_engine, org, "delete from usage_counters where organization_id=:o", o=org
    )

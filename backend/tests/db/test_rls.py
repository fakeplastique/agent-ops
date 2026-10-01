import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine

from app.db.session import tenant_session

pytestmark = pytest.mark.anyio

TENANT_TABLES = {"audit_logs", "outbox_events", "org_event_counters"}


async def _seed_audit(owner: AsyncEngine, org: uuid.UUID) -> None:
    # The owner is a superuser, so it can plant rows in any tenant for the app role to (not) see.
    async with owner.begin() as c:
        await c.execute(
            text(
                "insert into audit_logs (organization_id, entity_type, entity_id, action, actor_type)"
                " values (:o, 'decision', gen_random_uuid(), 'created', 'agent')"
            ),
            {"o": org},
        )


async def test_app_role_is_not_owner_nor_bypassrls(app_engine: AsyncEngine):
    async with app_engine.connect() as c:
        role = (
            await c.execute(
                text("select rolsuper, rolbypassrls from pg_roles where rolname = current_user")
            )
        ).one()
        owned = (
            await c.execute(
                text(
                    "select count(*) from pg_class where relnamespace = 'public'::regnamespace"
                    " and pg_get_userbyid(relowner) = current_user"
                )
            )
        ).scalar_one()
    assert tuple(role) == (False, False)
    assert owned == 0


async def test_query_without_tenant_context_returns_no_rows(
    app_engine: AsyncEngine, owner_engine: AsyncEngine, org_a, org_b
):
    await _seed_audit(owner_engine, org_a)
    async with app_engine.connect() as c:
        assert (await c.execute(text("select * from audit_logs"))).all() == []
    # FM3: another tenant's setting must not reveal A's rows either.
    async with tenant_session(app_engine, org_b) as s:
        assert (await s.execute(text("select * from audit_logs"))).all() == []
    # A reused connection keeps the setting as '' after the txn; that must read as "no tenant", not error.
    async with app_engine.connect() as c:
        await c.execute(
            text("select set_config('app.organization_id', :o, true)"), {"o": str(org_a)}
        )
        await c.commit()
        assert (await c.execute(text("select * from audit_logs"))).all() == []


async def test_insert_with_foreign_org_id_rejected(app_engine: AsyncEngine, org_a, org_b):
    with pytest.raises(DBAPIError, match="row-level security"):
        async with tenant_session(app_engine, org_a) as s:
            await s.execute(
                text(
                    "insert into audit_logs (organization_id, entity_type, entity_id, action, actor_type)"
                    " values (:o, 'decision', gen_random_uuid(), 'created', 'agent')"
                ),
                {"o": org_b},
            )


async def test_force_rls_on_every_tenant_table(owner_engine: AsyncEngine):
    async with owner_engine.connect() as c:
        rows = (
            await c.execute(
                text(
                    "select t.table_name, cl.relrowsecurity, cl.relforcerowsecurity,"
                    " exists (select 1 from pg_policies p where p.tablename = t.table_name) as has_policy"
                    " from information_schema.columns t"
                    " join pg_class cl on cl.oid = format('public.%I', t.table_name)::regclass"
                    " where t.table_schema = 'public' and t.column_name = 'organization_id'"
                )
            )
        ).all()
    assert {r[0] for r in rows} >= TENANT_TABLES  # guards against a vacuous pass
    assert [r[0] for r in rows if not all(r[1:])] == []

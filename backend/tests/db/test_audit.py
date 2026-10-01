import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine

from app.db.session import tenant_session
from app.services import audit

pytestmark = pytest.mark.anyio


async def _count(owner: AsyncEngine, org: uuid.UUID) -> int:
    async with owner.connect() as c:
        return (
            await c.execute(
                text("select count(*) from audit_logs where organization_id = :o"), {"o": org}
            )
        ).scalar_one()


async def _record(session, org):
    await audit.record(
        session, org, "decision", uuid.uuid4(), "created", "agent", uuid.uuid4(), {"k": 1}
    )


@pytest.mark.parametrize("sql", ["update audit_logs set action = 'x'", "delete from audit_logs"])
async def test_audit_update_and_delete_raise(app_engine, owner_engine, org_a, sql):
    async with tenant_session(app_engine, org_a) as s:
        await _record(s, org_a)
    # App role: the grant is missing.
    with pytest.raises(DBAPIError, match="permission denied"):
        async with tenant_session(app_engine, org_a) as s:
            await s.execute(text(sql))
    # Owner/superuser skips grants: only the trigger stands in the way.
    with pytest.raises(DBAPIError, match="append-only"):
        async with tenant_session(owner_engine, org_a) as s:
            await s.execute(text(sql))
    assert await _count(owner_engine, org_a) == 1


async def test_audit_insert_in_same_txn_rolls_back_together(app_engine, owner_engine, org_a):
    with pytest.raises(RuntimeError):
        async with tenant_session(app_engine, org_a) as s:
            await _record(s, org_a)
            raise RuntimeError("state change failed")
    assert await _count(owner_engine, org_a) == 0
    async with tenant_session(app_engine, org_a) as s:
        await _record(s, org_a)
    assert await _count(owner_engine, org_a) == 1

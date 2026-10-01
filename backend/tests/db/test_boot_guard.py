import pytest
from sqlalchemy.ext.asyncio import AsyncEngine

from app.db.guard import UnsafeDatabaseRoleError, assert_safe_role

pytestmark = pytest.mark.anyio


async def test_boot_fails_when_connected_as_owner(owner_engine: AsyncEngine):
    # The compose superuser owns the tables and bypasses RLS: exactly the mis-set DATABASE_URL.
    with pytest.raises(UnsafeDatabaseRoleError):
        await assert_safe_role(owner_engine)


async def test_boot_passes_as_app_role(app_engine: AsyncEngine):
    await assert_safe_role(app_engine)

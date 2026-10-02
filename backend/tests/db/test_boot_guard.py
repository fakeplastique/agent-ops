import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from sqlalchemy.pool import NullPool

from app.config import settings
from app.db.guard import UnsafeDatabaseRoleError, assert_safe_role
from app.main import app

pytestmark = pytest.mark.anyio


async def test_boot_fails_when_connected_as_owner(owner_engine: AsyncEngine):
    # The compose superuser owns the tables and bypasses RLS: exactly the mis-set DATABASE_URL.
    with pytest.raises(UnsafeDatabaseRoleError):
        await assert_safe_role(owner_engine)


async def test_boot_passes_as_app_role(app_engine: AsyncEngine):
    await assert_safe_role(app_engine)


def _url(engine: AsyncEngine) -> str:
    return engine.url.render_as_string(hide_password=False)


async def test_api_refuses_to_start_as_owner(monkeypatch: pytest.MonkeyPatch, owner_engine):
    monkeypatch.setattr(settings, "database_url", _url(owner_engine))
    with pytest.raises(UnsafeDatabaseRoleError):
        async with app.router.lifespan_context(app):
            pass


async def test_api_starts_as_app_role(monkeypatch: pytest.MonkeyPatch, app_engine):
    monkeypatch.setattr(settings, "database_url", _url(app_engine))
    async with app.router.lifespan_context(app):
        pass


async def _guard_as_probe(owner: AsyncEngine, attrs: str, owns_table: bool) -> None:
    """Run the guard as a throwaway role that has exactly one of the two unsafe traits."""
    async with owner.begin() as c:
        await c.execute(text("drop table if exists public.guard_probe"))
        await c.execute(text("drop role if exists guard_probe"))
        await c.execute(text(f"create role guard_probe login password 'x' {attrs}"))
        if owns_table:
            await c.execute(text("create table public.guard_probe (id int)"))
            await c.execute(text("alter table public.guard_probe owner to guard_probe"))
    probe = create_async_engine(
        owner.url.set(username="guard_probe", password="x"), poolclass=NullPool
    )
    try:
        await assert_safe_role(probe)
    finally:
        await probe.dispose()
        async with owner.begin() as c:
            await c.execute(text("drop table if exists public.guard_probe"))
            await c.execute(text("drop role guard_probe"))


async def test_boot_fails_for_bypassrls_role_that_owns_nothing(owner_engine: AsyncEngine):
    with pytest.raises(UnsafeDatabaseRoleError):
        await _guard_as_probe(owner_engine, "nosuperuser bypassrls", owns_table=False)


async def test_boot_fails_for_plain_role_that_owns_a_table(owner_engine: AsyncEngine):
    with pytest.raises(UnsafeDatabaseRoleError):
        await _guard_as_probe(owner_engine, "nosuperuser nobypassrls", owns_table=True)

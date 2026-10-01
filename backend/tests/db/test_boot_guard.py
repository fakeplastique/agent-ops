import pytest
from sqlalchemy.ext.asyncio import AsyncEngine

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

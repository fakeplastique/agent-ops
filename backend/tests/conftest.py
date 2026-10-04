import asyncio
import os
import uuid
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.engine import URL, make_url
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from sqlalchemy.pool import NullPool

from app.config import settings
from app.db.migrate import upgrade

ROLES_SQL = Path(__file__).parents[2] / "scripts" / "db_roles.sql"
TEST_DB = os.environ.get("TEST_DB_NAME", "agent_ops_test")  # a parallel session can use its own


def _urls() -> tuple[URL, URL]:
    # Owner = the compose superuser (migrations); app = the non-owner runtime role.
    owner = make_url(settings.migrate_database_url).set(database=TEST_DB)
    return owner, owner.set(username="app_rt", password="app_rt")


async def _prepare() -> None:
    owner, _ = _urls()
    admin = create_async_engine(owner.set(database="postgres"), isolation_level="AUTOCOMMIT")
    async with admin.connect() as c:
        exists = await c.scalar(
            text("select 1 from pg_database where datname = :n"), {"n": TEST_DB}
        )
        if not exists:
            await c.execute(text(f"create database {TEST_DB}"))
    await admin.dispose()
    engine = create_async_engine(owner, poolclass=NullPool)
    async with engine.begin() as c:
        await c.exec_driver_sql(ROLES_SQL.read_text())
        await c.exec_driver_sql("drop schema public cascade")
        await c.exec_driver_sql("create schema public")
        await c.run_sync(upgrade)
    await engine.dispose()


@pytest.fixture(scope="session", autouse=True)
def _migrated_db() -> None:
    asyncio.run(_prepare())


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
async def owner_engine() -> AsyncIterator[AsyncEngine]:
    engine = create_async_engine(_urls()[0], poolclass=NullPool)
    yield engine
    await engine.dispose()


@pytest.fixture
async def app_engine() -> AsyncIterator[AsyncEngine]:
    engine = create_async_engine(_urls()[1], poolclass=NullPool)
    yield engine
    await engine.dispose()


@pytest.fixture
async def worker_engine() -> AsyncIterator[AsyncEngine]:
    # app_worker is NOLOGIN everywhere: tests reach it with SET ROLE from the owner connection (a connect-time
    # setting), so no cluster-wide role is ever changed and no known-password login exists.
    engine = create_async_engine(
        _urls()[0], poolclass=NullPool, connect_args={"server_settings": {"role": "app_worker"}}
    )
    yield engine
    await engine.dispose()


@pytest.fixture
def org_a() -> uuid.UUID:
    return uuid.uuid4()


@pytest.fixture
def org_b() -> uuid.UUID:
    return uuid.uuid4()


@pytest.fixture
async def drained(app_engine: AsyncEngine, worker_engine: AsyncEngine) -> None:
    """The timeout sweep is global: clear other tests' overdue work first, so exact counts belong to a test.

    Sweeps (far in the future, so every leftover is due) until nothing is left to do. What a sweep leaves
    behind (stand-in steps) is due only after the year 2100, never in a test's clock."""
    from tests.integration.helpers import timeouts, utc

    for _ in range(20):
        out = await timeouts(app_engine, worker_engine, utc(2100, 1, 1))
        if out["steps"] == 0 and out["requests"] == 0:
            return


@pytest.fixture
async def web(app_engine: AsyncEngine, monkeypatch: pytest.MonkeyPatch):
    """The app wired to the test database, Redis database 15 and a fake issuer; Secure cookies flow over https."""
    import httpx
    from redis.asyncio import Redis

    from app.api import deps
    from app.config import settings
    from app.main import app
    from tests.integration.web_harness import (
        CLIENT_ID,
        ISSUER,
        ORIGIN,
        FakeIssuer,
        Harness,
        Tick,
        utc,
    )

    fake, clock = FakeIssuer(), Tick(utc(2026, 1, 15, 10, 0))
    redis = Redis.from_url("redis://localhost:6379/15")
    await redis.flushdb()
    monkeypatch.setattr(settings, "web_origin", ORIGIN)
    monkeypatch.setattr(settings, "oidc_issuer", ISSUER)
    monkeypatch.setattr(settings, "oidc_client_id", CLIENT_ID)
    monkeypatch.setattr(settings, "oidc_client_secret", "secret")
    app.dependency_overrides[deps.get_engine] = lambda: app_engine
    app.dependency_overrides[deps.get_redis] = lambda: redis
    app.dependency_overrides[deps.get_clock] = lambda: clock
    app.dependency_overrides[deps.get_oidc_transport] = lambda: fake.transport()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=ORIGIN) as client:
        yield Harness(client, fake, clock)
    app.dependency_overrides.clear()
    await redis.flushdb()
    await redis.aclose()

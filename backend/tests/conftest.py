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
        # Tests only: the role is NOLOGIN everywhere else until an environment gives it a password.
        await c.exec_driver_sql("alter role app_worker login password 'app_worker'")
        await c.exec_driver_sql("drop schema public cascade")
        await c.exec_driver_sql("create schema public")
        await c.run_sync(upgrade)
    await engine.dispose()


@pytest.fixture(scope="session", autouse=True)
def _migrated_db():
    asyncio.run(_prepare())
    yield
    asyncio.run(
        _worker_nologin()
    )  # roles are cluster-wide: do not leave a known-password login behind


async def _worker_nologin() -> None:
    engine = create_async_engine(_urls()[0], poolclass=NullPool)
    async with engine.begin() as c:
        await c.exec_driver_sql("alter role app_worker nologin")
    await engine.dispose()


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
    engine = create_async_engine(
        _urls()[0].set(username="app_worker", password="app_worker"), poolclass=NullPool
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

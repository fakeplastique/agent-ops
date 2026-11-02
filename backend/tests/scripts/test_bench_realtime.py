"""Smoke for scripts/bench_realtime.py: a real API, worker and demo data, 10 clients on every transport."""

import asyncio
import json
import os
import socket
import subprocess
import sys
import time
from collections.abc import Iterator
from pathlib import Path

import httpx2
import pytest
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from app.adapters.clock import SystemClock
from app.config import settings
from app.db.engine import make_engine
from app.db.migrate import upgrade
from app.services import demo
from tests.conftest import ROLES_SQL, TEST_DB, TEST_REDIS

BACKEND = Path(__file__).parents[2]
BENCH = BACKEND.parent / "scripts" / "bench_realtime.py"
BENCH_DB = f"{TEST_DB}_bench"


async def _build_db() -> None:
    owner = make_url(settings.migrate_database_url).set(database=BENCH_DB)
    admin = create_async_engine(owner.set(database="postgres"), isolation_level="AUTOCOMMIT")
    async with admin.connect() as c:
        await c.execute(text(f"drop database if exists {BENCH_DB} with (force)"))
        await c.execute(text(f"create database {BENCH_DB}"))
    await admin.dispose()
    engine = create_async_engine(owner, poolclass=NullPool)
    async with engine.begin() as c:
        await c.exec_driver_sql(ROLES_SQL.read_text())
        await c.run_sync(upgrade)
    await engine.dispose()
    rt = make_engine(owner.set(username="app_rt", password="app_rt").render_as_string(False))
    await demo.seed(rt, SystemClock())
    await rt.dispose()


async def _drop_db() -> None:
    admin = create_async_engine(
        make_url(settings.migrate_database_url).set(database="postgres"),
        isolation_level="AUTOCOMMIT",
    )
    async with admin.connect() as c:
        await c.execute(text(f"drop database if exists {BENCH_DB} with (force)"))
    await admin.dispose()


@pytest.fixture
def stack() -> Iterator[tuple[str, int]]:
    """(base url, api pid): uvicorn and the arq worker on a throwaway seeded database."""
    asyncio.run(_build_db())
    owner = make_url(settings.migrate_database_url).set(database=BENCH_DB)
    rt = owner.set(username="app_rt", password="app_rt")
    env = {
        **os.environ,
        "DATABASE_URL": rt.render_as_string(False),
        "MIGRATE_DATABASE_URL": owner.render_as_string(False),
        "WORKER_DATABASE_URL": owner.render_as_string(
            False
        ),  # app_worker is NOLOGIN: the owner scans
        "REDIS_URL": TEST_REDIS,
        "DEMO_MODE": "true",
    }
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    py = sys.executable
    api = subprocess.Popen(
        [py, "-m", "uvicorn", "app.main:app", "--port", str(port), "--log-level", "warning"],
        cwd=BACKEND, env=env,
    )  # fmt: skip
    worker = subprocess.Popen(
        [py, "-m", "arq", "app.worker.settings.WorkerSettings"], cwd=BACKEND, env=env
    )
    base = f"http://127.0.0.1:{port}"
    try:
        for _ in range(60):
            try:
                if httpx2.get(f"{base}/healthz").status_code == 200:
                    break
            except httpx2.TransportError:
                time.sleep(0.5)
        else:
            pytest.fail("the api did not come up")
        yield base, api.pid
    finally:
        for p in (api, worker):
            p.terminate()
        for p in (api, worker):
            try:
                p.wait(timeout=10)
            except subprocess.TimeoutExpired:  # an open stream can hold uvicorn's graceful stop
                p.kill()
        asyncio.run(_drop_db())


def test_smoke_prints_every_transport_with_all_metrics(stack: tuple[str, int]) -> None:
    base, pid = stack
    out = subprocess.run(
        [sys.executable, str(BENCH), "--base", base, "--smoke", "--pid", str(pid), "--json"],
        capture_output=True, text=True, timeout=240, check=False,
    )  # fmt: skip
    assert out.returncode == 0, out.stderr
    report = json.loads(out.stdout)
    assert report["clients"] == 10
    assert [r["transport"] for r in report["results"]] == ["ws", "longpoll", "adaptive"]
    for r in report["results"]:
        assert r["delivered"] == r["events"] * 10, r  # nothing lost, nobody missed an event
        assert r["latency_ms"]["p50"] > 0
        assert r["requests_per_min"] >= 0
        assert r["bytes_per_min"] > 0
        assert r["server_cpu_pct"] is not None
        assert r["server_rss_mb"] > 0


def test_a_non_local_base_is_refused_without_the_explicit_flag() -> None:
    # The bench signs in as demo users and flips a plan: pointed at a real deployment it would change it.
    out = subprocess.run(
        [sys.executable, str(BENCH), "--base", "https://agent-ops.example.com", "--smoke"],
        capture_output=True, text=True, timeout=30, check=False,
    )  # fmt: skip
    assert out.returncode != 0
    assert "--allow-remote" in out.stderr + out.stdout

from fastapi.testclient import TestClient
from httpx import ASGITransport, AsyncClient

from app.config import settings
from app.main import app


def test_healthz_ok():
    # Liveness must not touch Redis/Postgres, or a dependency outage restarts the API.
    assert TestClient(app).get("/healthz").json() == {"status": "ok"}


# Dependencies are real (compose); "down" means pointing the client at a closed port.
DEAD_PG = "postgresql+asyncpg://app:app@127.0.0.1:1/agent_ops"
DEAD_REDIS = "redis://127.0.0.1:1/0"


async def get_readyz(monkeypatch, **overrides):
    for key, value in overrides.items():
        monkeypatch.setattr(settings, key, value)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
        return await client.get("/readyz")


async def test_readyz_ok_with_real_dependencies(monkeypatch):
    assert (await get_readyz(monkeypatch)).status_code == 200


async def test_readyz_503_when_redis_down(monkeypatch):
    # Readiness must not report ok with a dead dependency.
    res = await get_readyz(monkeypatch, redis_url=DEAD_REDIS)
    assert res.status_code == 503
    assert res.json()["checks"] == {"postgres": True, "redis": False}


async def test_readyz_503_when_postgres_down(monkeypatch):
    res = await get_readyz(monkeypatch, database_url=DEAD_PG)
    assert res.status_code == 503
    assert res.json()["checks"] == {"postgres": False, "redis": True}

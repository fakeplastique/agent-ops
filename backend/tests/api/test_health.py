import pytest
from fastapi.testclient import TestClient

from app.config import Settings, settings
from app.main import app

client = TestClient(app)


def test_healthz_ok():
    # Liveness must not touch Redis/Postgres, or a dependency outage restarts the API.
    assert client.get("/healthz").json() == {"status": "ok"}


# Dependencies are real (compose); "down" means pointing the client at a closed port.
DEAD_PG = "postgresql+asyncpg://app:app@127.0.0.1:1/agent_ops"
DEAD_REDIS = "redis://127.0.0.1:1/0"


def test_readyz_ok_with_real_dependencies():
    assert client.get("/readyz").status_code == 200


def test_readyz_503_when_redis_down(monkeypatch):
    # Readiness must not report ok with a dead dependency.
    monkeypatch.setattr(settings, "redis_url", DEAD_REDIS)
    res = client.get("/readyz")
    assert res.status_code == 503
    assert res.json()["checks"] == {"postgres": True, "redis": False}


def test_readyz_503_when_postgres_down(monkeypatch):
    monkeypatch.setattr(settings, "database_url", DEAD_PG)
    res = client.get("/readyz")
    assert res.status_code == 503
    assert res.json()["checks"] == {"postgres": False, "redis": True}


@pytest.mark.parametrize(
    ("field", "value", "down"),
    [("database_url", "not a url", "postgres"), ("redis_url", "not a url", "redis")],
)
def test_readyz_503_not_500_on_malformed_url(monkeypatch, field, value, down):
    # A client that cannot even be built is still "not ready", never a 500 without checks.
    monkeypatch.setattr(settings, field, value)
    res = client.get("/readyz")
    assert res.status_code == 503
    assert res.json()["checks"][down] is False


def test_plain_postgres_url_is_normalised_for_asyncpg():
    # Railway hands out postgresql://; SQLAlchemy would load psycopg2 without this.
    assert Settings(database_url="postgresql://u:p@h/db").database_url.startswith(
        "postgresql+asyncpg://"
    )

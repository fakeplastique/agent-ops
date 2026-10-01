from fastapi.testclient import TestClient

from app.main import app


def test_healthz_ok():
    # Liveness must not touch Redis/Postgres, or a dependency outage restarts the API.
    assert TestClient(app).get("/healthz").json() == {"status": "ok"}

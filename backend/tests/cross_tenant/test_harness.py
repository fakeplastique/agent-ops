from fastapi import FastAPI

from app.main import app

from .harness import unregistered


def test_cross_tenant_fixture_registers_routes():
    probe = FastAPI()

    @probe.get("/api/v1/decisions/{id}")
    def _read(id: str): ...

    # Harness has to notice a route nobody put in the table.
    assert unregistered(probe) == [("GET", "/api/v1/decisions/{id}")]


def test_every_route_is_registered():
    assert unregistered(app) == []

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


def test_the_route_walk_sees_the_apps_routes():
    # catches: a guard that passes because its route walk finds nothing (included routers are wrapped)
    from .harness import api_routes

    paths = {path for path, _ in api_routes(app)}
    assert {
        "/healthz",
        "/readyz",
        "/api/v1/auth/login",
        "/api/v1/auth/logout",
        "/api/v1/auth/organization",
    } <= paths
    assert len(paths) >= 7

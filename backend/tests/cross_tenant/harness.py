from fastapi import FastAPI
from fastapi.routing import APIRoute

# Every tenant-facing route belongs here: (method, path) -> how tenant B probes A's id.
# Later slices add rows; a route in neither table fails test_every_route_is_registered.
ROUTES: dict[tuple[str, str], str] = {}

# Routes that carry no tenant data.
EXEMPT: set[str] = {"/healthz", "/readyz"}


def unregistered(app: FastAPI) -> list[tuple[str, str]]:
    registered = {path for _, path in ROUTES}
    return sorted(
        (m, r.path)
        for r in app.routes
        if isinstance(r, APIRoute) and r.path not in EXEMPT and r.path not in registered
        for m in (r.methods or ())
    )

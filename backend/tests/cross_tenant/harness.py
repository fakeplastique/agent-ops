from fastapi import FastAPI
from fastapi.routing import APIRoute

# Every tenant-facing route belongs here: (method, path) -> how tenant B probes A's id.
# Later slices add rows; a route in neither table fails test_every_route_is_registered.
ROUTES: dict[tuple[str, str], str] = {}

# Routes that carry no tenant data.
EXEMPT: set[str] = {
    "/healthz",
    "/readyz",
    # Auth routes carry no tenant data: they resolve a login, a session, or switch the caller's own organization.
    "/api/v1/auth/login",
    "/api/v1/auth/callback",
    "/api/v1/auth/logout",
    "/api/v1/auth/session",
    "/api/v1/auth/organization",
}


def unregistered(app: FastAPI) -> list[tuple[str, str]]:
    registered = {path for _, path in ROUTES}
    return sorted(
        (m, r.path)
        for r in app.routes
        if isinstance(r, APIRoute) and r.path not in EXEMPT and r.path not in registered
        for m in (r.methods or ())
    )

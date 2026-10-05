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
    "/api/v1/me",  # the caller's own principal, no id parameter to swap
}


def api_routes(router, prefix: str = ""):
    """Every APIRoute with its full path, descending into included routers.

    FastAPI wraps an included router, so `isinstance(r, APIRoute)` over `app.routes` finds nothing: a guard
    built on that walk passes while looking at no route at all (test_the_route_walk_sees_the_apps_routes)."""
    for r in router.routes:
        if isinstance(r, APIRoute):
            yield prefix + r.path, r
        elif hasattr(r, "original_router"):
            yield from api_routes(r.original_router, prefix + r.include_context.prefix)


def unregistered(app: FastAPI) -> list[tuple[str, str]]:
    registered = {path for _, path in ROUTES}
    return sorted(
        (m, path)
        for path, r in api_routes(app)
        if path not in EXEMPT and path not in registered
        for m in (r.methods or ())
    )

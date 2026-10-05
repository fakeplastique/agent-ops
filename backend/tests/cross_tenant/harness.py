from fastapi import FastAPI
from fastapi.routing import APIRoute

# Every tenant-facing route belongs here: (method, path) -> how tenant B probes A's id.
# Later slices add rows; a route in neither table fails test_every_route_is_registered.
ROUTES: dict[tuple[str, str], str] = {
    (
        "GET",
        "/api/v1/organizations/{organization_id}",
    ): "tenant B asks for tenant A's organization id",
    ("DELETE", "/api/v1/invitations/{invitation_id}"): "tenant B revokes tenant A's invitation id",
    ("PATCH", "/api/v1/members/{user_id}"): "tenant B changes tenant A's member",
    ("DELETE", "/api/v1/members/{user_id}"): "tenant B removes tenant A's member",
    # Probed by tests/cross_tenant/test_decisions.py.
    (
        "GET",
        "/api/v1/decision-requests/{decision_request_id}",
    ): "tenant B reads tenant A's request",
    (
        "POST",
        "/api/v1/decision-requests/{decision_request_id}/revise",
    ): "tenant B's agent revises tenant A's request",
    (
        "POST",
        "/api/v1/decision-requests/{decision_request_id}/withdraw",
    ): "tenant B's agent or admin withdraws tenant A's request",
    (
        "GET",
        "/api/v1/decision-requests/{decision_request_id}/votes",
    ): "tenant B reads tenant A's votes",
    (
        "POST",
        "/api/v1/decision-requests/{decision_request_id}/votes",
    ): "tenant B's agent votes on tenant A's request",
    (
        "GET",
        "/api/v1/decision-requests/{decision_request_id}/messages",
    ): "tenant B reads tenant A's discussion",
    (
        "POST",
        "/api/v1/decision-requests/{decision_request_id}/messages",
    ): "tenant B posts into tenant A's discussion",
    # Probed by tests/cross_tenant/test_steps_receipts.py.
    (
        "POST",
        "/api/v1/approval-steps/{step_id}/decide",
    ): "tenant B's user decides tenant A's step",
    (
        "GET",
        "/api/v1/decision-requests/{decision_request_id}/receipt",
    ): "tenant B reads tenant A's receipt",
}

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
    # Organization/invitation/member lists and creates take no foreign id: the tenant comes from the caller.
    "/api/v1/organizations",
    "/api/v1/invitations",
    "/api/v1/invitations/accept",
    "/api/v1/members",
    "/api/v1/me",  # the caller's own principal, no id parameter to swap
    "/api/v1/decision-requests",  # create and list: the tenant comes from the caller
    "/api/v1/me/pending-approvals",  # the caller's own inbox, no id parameter
    # Public by design (PRODUCT 3.2): answers only valid true/false, whoever's receipt it is.
    "/api/v1/receipts/verify",
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

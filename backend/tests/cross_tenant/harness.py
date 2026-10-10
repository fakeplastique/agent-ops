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
    # S21, probed with every principal by tests/cross_tenant/test_rest_routes.py.
    ("GET", "/api/v1/agents/{agent_id}"): "tenant B reads tenant A's agent",
    ("DELETE", "/api/v1/agents/{agent_id}"): "tenant B revokes tenant A's agent",
    ("POST", "/api/v1/agents/{agent_id}/rotate"): "tenant B mints a token for tenant A's agent",
    ("GET", "/api/v1/teams/{team_id}"): "tenant B reads tenant A's team",
    ("PATCH", "/api/v1/teams/{team_id}"): "tenant B renames or moves tenant A's team",
    ("DELETE", "/api/v1/teams/{team_id}"): "tenant B deletes tenant A's team",
    ("GET", "/api/v1/escalation-policies/{policy_id}"): "tenant B reads tenant A's policy",
    ("PATCH", "/api/v1/escalation-policies/{policy_id}"): "tenant B edits tenant A's policy",
    ("DELETE", "/api/v1/escalation-policies/{policy_id}"): "tenant B deletes tenant A's policy",
    ("GET", "/api/v1/me/tokens/{token_id}"): "tenant B reads tenant A's personal token",
    ("DELETE", "/api/v1/me/tokens/{token_id}"): "tenant B revokes tenant A's personal token",
    # S27, probed with every principal by tests/cross_tenant/test_rest_routes.py.
    ("GET", "/api/v1/webhook-subscriptions/{subscription_id}"): "tenant B reads tenant A's webhook",
    (
        "PATCH",
        "/api/v1/webhook-subscriptions/{subscription_id}",
    ): "tenant B repoints or mutes tenant A's webhook",
    (
        "DELETE",
        "/api/v1/webhook-subscriptions/{subscription_id}",
    ): "tenant B deletes tenant A's webhook",
    (
        "POST",
        "/api/v1/webhook-subscriptions/{subscription_id}/rotate-secret",
    ): "tenant B mints a signing secret for tenant A's webhook",
    # S28, probed with every principal by tests/cross_tenant/test_rest_routes.py.
    (
        "GET",
        "/api/v1/webhook-subscriptions/{subscription_id}/deliveries",
    ): "tenant B reads tenant A's delivery log",
    (
        "POST",
        "/api/v1/webhook-subscriptions/{subscription_id}/deliveries/{delivery_id}/redeliver",
    ): "tenant B re-sends tenant A's events",
}

# Routes that carry no tenant data.
EXEMPT: set[str] = {
    "/healthz",
    "/readyz",
    "/metrics",  # process-wide counters by cache name, behind METRICS_TOKEN; no tenant id in or out
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
    # The tenant comes from the caller; a foreign decision_request_id filter is empty (tests/api/test_audit.py).
    "/api/v1/audit-logs",
    "/api/v1/agents",  # create and list: the tenant comes from the caller
    "/api/v1/teams",  # same; a foreign parent_team_id is 404 (tests/api/test_teams.py)
    "/api/v1/me/tokens",  # the caller's own tokens: create and list take no foreign id
    "/api/v1/escalation-policies",  # same; a foreign team_id is 404 (tests/api/test_policies.py)
    "/api/v1/organizations/current/plan",  # the caller's own organization, no id parameter
    "/api/v1/webhook-subscriptions",  # create and list: the tenant comes from the caller
    # Aggregates of the caller's own organization, no id parameter (tests/cross_tenant/test_secondary_reads.py).
    "/api/v1/stats/summary",
    "/api/v1/stats/timeseries",
    # Cross-tenant by design for platform admins; everyone else gets 403 before any id is read
    # (tests/integration/test_flag_paths.py::test_platform_admin_only_from_env_allowlist).
    "/api/v1/platform/organizations/{organization_id}/plan",
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
    # (method, path): a new method on a listed path is a new way in and needs its own row
    return sorted(
        (m, path)
        for path, r in api_routes(app)
        if path not in EXEMPT
        for m in (r.methods or ())
        if (m, path) not in ROUTES
    )

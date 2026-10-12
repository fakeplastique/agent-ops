"""The published contract (spec 5.2): where it is served, what it leaks, which errors it admits to."""

import re

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)
SPEC = "/api/v1/openapi.json"
# Shown once, at creation or rotation: the only operations whose response may carry a raw `token`.
TOKEN_ONCE = {
    ("post", "/api/v1/agents"),
    ("post", "/api/v1/agents/{agent_id}/rotate"),
    ("post", "/api/v1/invitations"),
    ("post", "/api/v1/me/tokens"),
}
SECRET_ONCE = {  # webhook signing secrets: same rule as tokens
    ("post", "/api/v1/webhook-subscriptions"),
    ("post", "/api/v1/webhook-subscriptions/{subscription_id}/rotate-secret"),
}
SENSITIVE = re.compile(
    r"token|key|secret|hash|digest|ciphertext|password|prefix|salt|signature", re.IGNORECASE
)
# Every (schema, field) a SENSITIVE name may appear as; anything else in a response is a leak.
ALLOWED = {
    ("AgentCreated", "token"),  # POST /agents: the new agent's token, shown once
    ("AgentToken", "token"),  # POST /agents/{id}/rotate: the replacement token, shown once
    ("InvitationCreated", "token"),  # POST /invitations: the invite link token, shown once
    ("PatCreated", "token"),  # POST /me/tokens: the new PAT, shown once
    ("WebhookCreated", "secret"),  # POST /webhook-subscriptions: the signing secret, shown once
    (
        "WebhookSecret",
        "secret",
    ),  # POST /webhook-subscriptions/{id}/rotate-secret: the new one, once
    (
        "DecisionOut",
        "content_hash",
    ),  # digest of the request content a receipt signs: public by design
    ("ReceiptOut", "signature"),  # the receipt's own signature: holders need it to verify
    ("OrganizationOut", "plan_key"),  # the plan's name (free/pro/enterprise), not a credential
}
# Only the service knows these, so the table is by hand; the test fails on a doc that drifts either way.
CONFLICT = {
    ("post", "/api/v1/organizations"),
    ("post", "/api/v1/invitations/accept"),
    ("patch", "/api/v1/members/{user_id}"),
    ("delete", "/api/v1/members/{user_id}"),
    ("post", "/api/v1/decision-requests"),
    ("post", "/api/v1/decision-requests/{decision_request_id}/revise"),
    ("post", "/api/v1/decision-requests/{decision_request_id}/withdraw"),
    ("post", "/api/v1/decision-requests/{decision_request_id}/votes"),
    ("post", "/api/v1/decision-requests/{decision_request_id}/messages"),
    ("post", "/api/v1/approval-steps/{step_id}/decide"),
    ("post", "/api/v1/agents"),
    ("post", "/api/v1/teams"),
    ("patch", "/api/v1/teams/{team_id}"),
    ("delete", "/api/v1/teams/{team_id}"),
    ("post", "/api/v1/escalation-policies"),
}
PLAN = {("post", "/api/v1/decision-requests"), ("post", "/api/v1/escalation-policies")}
PLAN |= {("get", "/api/v1/stats/summary"), ("get", "/api/v1/stats/timeseries")}  # analytics gate
PLAN |= {("post", "/api/v1/me/telegram/link-code")}  # flag `telegram`
PUBLIC = {"/healthz", "/readyz", "/api/v1/auth/login", "/api/v1/auth/callback"}
PUBLIC |= {"/api/v1/auth/logout", "/api/v1/receipts/verify"}
PUBLIC |= {"/api/v1/auth/demo/users", "/api/v1/auth/demo/login"}  # DEMO_MODE only, 404 otherwise
UNLIMITED = {"/api/v1/auth/logout", "/api/v1/auth/organization"}  # session-only, no write limit
NO_ROLE = {"/api/v1/auth/session", "/api/v1/me"}  # identity reads: any valid credential passes


def _spec() -> dict:
    r = client.get(SPEC)
    assert r.status_code == 200, r.text
    return r.json()


def _ops(spec: dict):
    for path, ops in spec["paths"].items():
        for method, op in ops.items():
            yield method, path, op


def _schemas(spec: dict, node, seen: set[str]) -> set[str]:
    """Every component schema reachable from `node`."""
    if isinstance(node, dict):
        ref = node.get("$ref")
        if ref and (name := ref.rsplit("/", 1)[-1]) not in seen:
            seen.add(name)
            _schemas(spec, spec["components"]["schemas"][name], seen)
        for v in node.values():
            _schemas(spec, v, seen)
    elif isinstance(node, list):
        for v in node:
            _schemas(spec, v, seen)
    return seen


def _named_fields(node, owner: str, out: set[tuple[str, str]]) -> None:
    """(schema, field) for every property name under `node`: nested objects, items, additionalProperties, $defs."""
    if isinstance(node, dict):
        for name in node.get("properties", {}):
            out.add((owner, name))
        for v in node.values():
            _named_fields(v, owner, out)
    elif isinstance(node, list):
        for v in node:
            _named_fields(v, owner, out)


def _fields(spec: dict, op: dict) -> set[tuple[str, str]]:
    out: set[tuple[str, str]] = set()
    _named_fields(op["responses"], "<inline>", out)
    for name in _schemas(spec, op["responses"], set()):
        _named_fields(spec["components"]["schemas"][name], name, out)
    return out


def test_openapi_and_docs_are_served_under_the_api_prefix():
    assert _spec()["info"]["title"] == "Agent Ops"
    docs = client.get("/docs")
    assert docs.status_code == 200 and SPEC in docs.text
    assert client.get("/openapi.json").status_code == 404  # one contract, one address


def test_no_response_schema_exposes_hash_secret_or_ciphertext_fields():
    spec = _spec()
    with_token, with_secret, seen = set(), set(), set()
    for method, path, op in _ops(spec):
        fields = _fields(spec, op)
        seen |= fields
        leaked = {f for f in fields if SENSITIVE.search(f[1])} - ALLOWED
        assert not leaked, (method, path, leaked)
        if any(f == "token" for _, f in fields):
            with_token.add((method, path))
        if any(f == "secret" for _, f in fields):
            with_secret.add((method, path))
    assert with_token == TOKEN_ONCE
    assert with_secret == SECRET_ONCE
    assert ALLOWED <= seen, ALLOWED - seen  # a stale allowance would excuse a future leak


def test_every_route_documents_error_responses():
    spec = _spec()
    for method, path, op in _ops(spec):
        documented = {int(c) for c in op["responses"] if c.startswith("4")}
        want = set()
        if path not in PUBLIC:
            want.add(401)
        if path not in PUBLIC | NO_ROLE:
            want.add(403)
        if "{" in path:
            want.add(404)
        if op.get("parameters") or op.get("requestBody"):
            want.add(422)
        if method != "get" and path not in UNLIMITED:
            want.add(429)
        if (method, path) in CONFLICT:
            want.add(409)
        if (method, path) in PLAN:
            want.add(402)
        assert want <= documented, (method, path, want - documented)
        # 402 and 409 come from the service: claiming one the route cannot raise is drift too
        assert (409 in documented) == ((method, path) in CONFLICT), (method, path)
        assert (402 in documented) == ((method, path) in PLAN), (method, path)
        for code in documented:  # the envelope from app/api/errors.py, never FastAPI's own
            ref = op["responses"][str(code)]["content"]["application/json"]["schema"]["$ref"]
            assert ref.endswith("/ErrorBody"), (method, path, code, ref)
    assert "HTTPValidationError" not in spec["components"]["schemas"]

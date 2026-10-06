"""The published contract (spec 5.2): where it is served, what it leaks, which errors it admits to."""

import re

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)
SPEC = "/api/v1/openapi.json"
# Shown once, at creation or rotation: the only response schemas allowed to carry a raw `token`.
TOKEN_ONCE = {
    ("post", "/api/v1/agents"),
    ("post", "/api/v1/agents/{agent_id}/rotate"),
    ("post", "/api/v1/invitations"),
    ("post", "/api/v1/me/tokens"),
}
STORAGE = re.compile(r"(^|_)(hash|digest|secret|ciphertext|password|prefix|salt)($|_)")
PUBLIC_FIELDS = {"content_hash"}  # the signed request digest a receipt carries: public on purpose
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
PUBLIC = {"/healthz", "/readyz", "/api/v1/auth/login", "/api/v1/auth/callback"}
PUBLIC |= {"/api/v1/auth/logout", "/api/v1/receipts/verify"}
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


def _fields(spec: dict, names: set[str]) -> set[str]:
    return {f for n in names for f in spec["components"]["schemas"][n].get("properties", {})}


def test_openapi_and_docs_are_served_under_the_api_prefix():
    assert _spec()["info"]["title"] == "Agent Ops"
    docs = client.get("/docs")
    assert docs.status_code == 200 and SPEC in docs.text
    assert client.get("/openapi.json").status_code == 404  # one contract, one address


def test_no_response_schema_exposes_hash_secret_or_ciphertext_fields():
    spec = _spec()
    with_token = set()
    for method, path, op in _ops(spec):
        fields = _fields(spec, _schemas(spec, op["responses"], set()))
        leaked = {f for f in fields if STORAGE.search(f)} - PUBLIC_FIELDS
        assert not leaked, (method, path, leaked)
        if "token" in fields:
            with_token.add((method, path))
    assert with_token == TOKEN_ONCE


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

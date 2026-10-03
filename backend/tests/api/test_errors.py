import uuid

from fastapi import APIRouter
from fastapi.testclient import TestClient

from app.api.deps import get_current_principal
from app.authz.matrix import Role
from app.authz.principals import UserPrincipal
from app.domain import errors
from app.domain.errors import (
    IllegalTransitionError,
    InvalidInput,
    NotFoundError,
    PlanLimitReached,
)
from app.main import app

api_router = APIRouter(prefix="/test-errors")


@api_router.get("/domain-error/{error_name}")
def trigger_domain_error(error_name: str):
    err_cls = getattr(errors, error_name)
    raise err_cls(f"Sample error for {error_name}")


@api_router.get("/crash")
def trigger_crash():
    raise RuntimeError("SELECT * FROM secrets WHERE key = 'leaked_secret'")


app.include_router(api_router)
client = TestClient(app, raise_server_exceptions=False)


def test_every_domain_error_maps_to_code_status_and_envelope():
    cases = [
        (NotFoundError, 404, "not_found"),
        (PlanLimitReached, 402, "plan_limit_reached"),
        (InvalidInput, 422, "invalid_input"),
        (IllegalTransitionError, 409, "illegal_transition"),
    ]
    for cls, expected_status, expected_code in cases:
        res = client.get(f"/test-errors/domain-error/{cls.__name__}")
        assert res.status_code == expected_status, f"{cls.__name__} returned {res.status_code}"
        data = res.json()
        assert "error" in data
        assert data["error"]["code"] == expected_code
        assert data["error"]["message"] == f"Sample error for {cls.__name__}"
        assert "details" in data["error"]


def test_500_body_has_request_id_and_no_internals():
    res = client.get("/test-errors/crash")
    assert res.status_code == 500
    data = res.json()
    assert "error" in data
    assert data["error"]["code"] == "internal_error"
    assert "request_id" in data["error"]["details"]
    # Must not leak stack traces or internal secrets/SQL
    assert "secrets" not in res.text
    assert "Traceback" not in res.text
    assert "RuntimeError" not in res.text


def test_me_unauthenticated_returns_401():
    res = client.get("/api/v1/me")
    assert res.status_code == 401
    body = res.json()
    assert body["error"]["code"] == "unauthorized"


def test_me_authenticated_returns_principal_info():
    user_id = uuid.uuid4()
    org_id = uuid.uuid4()
    mock_principal = UserPrincipal(user_id=user_id, organization_id=org_id, role=Role.ORG_ADMIN)

    app.dependency_overrides[get_current_principal] = lambda: mock_principal
    try:
        res = client.get("/api/v1/me")
        assert res.status_code == 200
        body = res.json()
        assert body["type"] == "user"
        assert body["id"] == str(user_id)
        assert body["organization_id"] == str(org_id)
        assert body["role"] == "org_admin"
    finally:
        app.dependency_overrides.pop(get_current_principal, None)

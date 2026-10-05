import uuid

from fastapi import APIRouter
from fastapi.testclient import TestClient

from app.api.deps import get_current_principal
from app.api.errors import DOMAIN_STATUS_CODES
from app.authz.matrix import Role
from app.authz.principals import UserPrincipal
from app.domain import errors
from app.domain.errors import DomainError
from app.main import app
from tests.api.harness import probe_client

api_router = APIRouter(prefix="/test-errors")


@api_router.get("/domain-error/{error_name}")
def trigger_domain_error(error_name: str):
    err_cls = getattr(errors, error_name)
    try:
        raise err_cls(f"Sample error for {error_name}")
    except TypeError:  # errors with a fixed message take no argument
        raise err_cls() from None


@api_router.get("/crash")
def trigger_crash():
    raise RuntimeError("SELECT * FROM secrets WHERE key = 'leaked_secret'")


probe = probe_client(api_router)
client = TestClient(app, raise_server_exceptions=False)


def test_production_app_not_polluted_by_test_routes():
    route_paths = [getattr(route, "path", "") for route in app.routes]
    assert not any(p.startswith("/test-") for p in route_paths), f"Found test routes: {route_paths}"


def _all_domain_errors() -> list[type[DomainError]]:
    found, todo = [], [DomainError]
    while todo:
        for sub in todo.pop().__subclasses__():
            found.append(sub)
            todo.append(sub)
    return found


def test_every_domain_error_maps_to_code_status_and_envelope():
    for cls in _all_domain_errors():
        res = probe.get(f"/test-errors/domain-error/{cls.__name__}")
        assert res.status_code == DOMAIN_STATUS_CODES[cls], cls.__name__
        err = res.json()["error"]
        assert err["code"] == cls.code
        assert err["message"] and "details" in err


def test_500_body_has_request_id_and_no_internals():
    res = probe.get("/test-errors/crash")
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
    assert body["error"]["code"] == "unauthenticated"


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

from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient

from app.api.errors import register_error_handlers
from app.api.limits import BodyLimitMiddleware


def probe_client(router: APIRouter) -> TestClient:
    """Test routes live on a throwaway app wired like production, never on `app.main.app`."""
    probe = FastAPI()
    probe.add_middleware(BodyLimitMiddleware)
    register_error_handlers(probe)
    probe.include_router(router)
    return TestClient(probe, raise_server_exceptions=False)

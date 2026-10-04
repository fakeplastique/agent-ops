import uuid

from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient

from app.api.errors import register_error_handlers
from app.api.limits import BodyLimitMiddleware
from app.db.session import tenant_session
from app.services.tokens import create_pat, issue_agent_token


def probe_client(router: APIRouter) -> TestClient:
    """Test routes live on a throwaway app wired like production, never on `app.main.app`."""
    probe = FastAPI()
    probe.add_middleware(BodyLimitMiddleware)
    register_error_handlers(probe)
    probe.include_router(router)
    return TestClient(probe, raise_server_exceptions=False)


DR = "/api/v1/decision-requests"
BODY = {
    "title": "Refund order 7",
    "description": "d",
    "action_type": "refund",
    "payload": {"amount": 5},
    "risk_level": "low",
}


async def agent_auth(app_engine, org: uuid.UUID, identity: uuid.UUID) -> dict[str, str]:
    async with tenant_session(app_engine, org) as s:
        token = await issue_agent_token(s, org, identity)
    return {"Authorization": f"Bearer {token}"}


async def user_auth(app_engine, org: uuid.UUID, user: uuid.UUID) -> dict[str, str]:
    async with tenant_session(app_engine, org) as s:
        _, token = await create_pat(s, org, user, "t")
    return {"Authorization": f"Bearer {token}"}

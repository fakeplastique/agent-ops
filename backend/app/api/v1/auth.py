import uuid
from typing import Any

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, ConfigDict
from sqlalchemy.ext.asyncio import AsyncEngine

from app.api.deps import (
    COOKIE,
    ClockDep,
    EngineDep,
    OidcDep,
    RedisDep,
    SessionDep,
    public_limit,
)
from app.api.errors import error_responses
from app.authz.origin import require_same_origin
from app.config import settings
from app.domain.errors import NotFoundError
from app.services import demo, oidc, web_sessions
from app.services.web_sessions import ABSOLUTE, WebSession

router = APIRouter(prefix="/auth", tags=["auth"])


STATE_COOKIE = "aop_oidc_state"
FLOW_PATH = "/api/v1/auth"


def _set_cookie(response: Response, value: str) -> None:
    # HttpOnly (no script reads it), Secure, SameSite=Lax (not sent on cross-site POSTs), the 30-day absolute cap.
    response.set_cookie(
        COOKIE,
        value,
        max_age=int(ABSOLUTE.total_seconds()),
        httponly=True,
        secure=True,
        samesite="lax",
        path="/",
    )


class OrganizationIn(BaseModel):
    model_config = ConfigDict(extra="forbid")  # unknown fields are a 422 (spec 5.2)
    organization_id: uuid.UUID


@router.get("/login")
async def login(cfg: OidcDep, redis: RedisDep) -> RedirectResponse:
    url, state = await oidc.begin_login(cfg, redis)
    response = RedirectResponse(url, status_code=302)
    # Binds the flow to this browser (login CSRF). Lax: it is sent on the top-level GET back from the issuer.
    response.set_cookie(
        STATE_COOKIE,
        state,
        max_age=oidc.FLOW_TTL,
        httponly=True,
        secure=True,
        samesite="lax",
        path=FLOW_PATH,
    )
    return response


@router.get("/callback", responses=error_responses(400))
async def callback(
    request: Request,
    cfg: OidcDep,
    redis: RedisDep,
    engine: EngineDep,
    clock: ClockDep,
    code: str = "",
    state: str = "",
) -> RedirectResponse:
    who = await oidc.complete_login(cfg, redis, code, state, request.cookies.get(STATE_COOKIE))
    user_id = await web_sessions.find_or_create_user(engine, who.email, who.name)
    raw = await web_sessions.start_session(engine, user_id, clock, request.cookies.get(COOKIE))
    response = RedirectResponse(f"{settings.web_origin}/", status_code=302)
    _set_cookie(response, raw)
    response.delete_cookie(STATE_COOKIE, path=FLOW_PATH, secure=True, httponly=True, samesite="lax")
    return response


@router.post("/logout", status_code=204, dependencies=[Depends(require_same_origin)])
async def logout(request: Request, engine: EngineDep) -> Response:
    await web_sessions.end_session(
        engine, request.cookies.get(COOKIE)
    )  # the row, not just the cookie
    response = Response(status_code=204)
    response.delete_cookie(COOKIE, path="/", secure=True, httponly=True, samesite="lax")
    return response


async def _summary(engine: AsyncEngine, session: WebSession) -> dict[str, Any]:
    org, role = session.selected_organization_id, None
    if org is not None:
        role = await web_sessions.role_in(
            engine, session.user_id, org
        )  # read now: a removed member has none
        if role is None:
            org = None
    return {
        "user_id": str(session.user_id),
        "organization_id": str(org) if org else None,
        "role": role,
    }


@router.get("/session")
async def current_session(session: SessionDep, engine: EngineDep) -> dict[str, Any]:
    return await _summary(engine, session)


@router.post("/organization", dependencies=[Depends(require_same_origin)])
async def select_organization(
    body: OrganizationIn, request: Request, session: SessionDep, engine: EngineDep, clock: ClockDep
) -> dict[str, Any]:
    raw = request.cookies[COOKIE]
    await web_sessions.select_organization(engine, raw, session.user_id, body.organization_id)
    refreshed = await web_sessions.load_session(engine, raw, clock)
    return await _summary(engine, refreshed or session)


async def demo_only() -> None:
    """The demo routes do not exist (404) unless DEMO_MODE is on."""
    if not settings.demo_mode:
        raise NotFoundError


class DemoLoginIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    email: str


@router.get("/demo/users", dependencies=[Depends(demo_only)], responses=error_responses(404))
async def demo_users(engine: EngineDep) -> dict[str, Any]:
    """The seeded users the login page offers as "Sign in as" buttons."""
    return {"users": await demo.seeded_users(engine)}


@router.post(
    "/demo/login",
    dependencies=[Depends(demo_only), Depends(require_same_origin), Depends(public_limit)],
    responses=error_responses(404),
)
async def demo_login(
    body: DemoLoginIn, request: Request, response: Response, engine: EngineDep, clock: ClockDep
) -> dict[str, Any]:
    raw, user, org, role = await demo.sign_in(
        engine, clock, body.email, request.cookies.get(COOKIE)
    )
    _set_cookie(response, raw)
    return {"user_id": str(user), "organization_id": str(org), "role": role}

import uuid
from typing import Any

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, ConfigDict
from sqlalchemy.ext.asyncio import AsyncEngine

from app.api.deps import COOKIE, ClockDep, EngineDep, OidcDep, RedisDep, SessionDep
from app.authz.origin import require_same_origin
from app.config import settings
from app.services import oidc, web_sessions
from app.services.web_sessions import ABSOLUTE, WebSession

router = APIRouter(prefix="/auth", tags=["auth"])


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
    return RedirectResponse(await oidc.begin_login(cfg, redis), status_code=302)


@router.get("/callback")
async def callback(
    request: Request,
    cfg: OidcDep,
    redis: RedisDep,
    engine: EngineDep,
    clock: ClockDep,
    code: str = "",
    state: str = "",
) -> RedirectResponse:
    who = await oidc.complete_login(cfg, redis, code, state)
    user_id = await web_sessions.find_or_create_user(engine, who.email, who.name)
    raw = await web_sessions.start_session(engine, user_id, clock, request.cookies.get(COOKIE))
    response = RedirectResponse(f"{settings.web_origin}/", status_code=302)
    _set_cookie(response, raw)
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

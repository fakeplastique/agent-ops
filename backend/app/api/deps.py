from typing import Annotated

import httpx2
from fastapi import Depends, Header, Request
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncEngine

from app.adapters.clock import SystemClock
from app.api.ratelimit import auth_failed, check_ip, check_user_write, check_write
from app.authz.matrix import Action, Role, can
from app.authz.origin import require_same_origin
from app.authz.principals import Principal, UserPrincipal
from app.authz.resolver import resolve_bearer
from app.config import settings
from app.db.engine import make_engine
from app.domain.errors import Forbidden, LoginUnavailable, Unauthenticated
from app.ports.clock import Clock
from app.services.oidc import OidcConfig
from app.services.web_sessions import WebSession, load_session, role_in

COOKIE = "aop_session"
_engine: AsyncEngine | None = None
_redis: Redis | None = None


def get_engine() -> AsyncEngine:
    global _engine
    if _engine is None:
        _engine = make_engine(settings.database_url)
    return _engine


def get_redis() -> Redis:
    global _redis
    if _redis is None:
        _redis = Redis.from_url(settings.redis_url)
    return _redis


def get_clock() -> Clock:
    return SystemClock()


def get_oidc_transport() -> httpx2.AsyncBaseTransport | None:
    return None  # tests override this with an in-process issuer


def get_oidc(
    transport: Annotated[httpx2.AsyncBaseTransport | None, Depends(get_oidc_transport)],
) -> OidcConfig:
    if not settings.oidc_client_id:
        raise LoginUnavailable
    return OidcConfig(
        issuer=settings.oidc_issuer,
        client_id=settings.oidc_client_id,
        client_secret=settings.oidc_client_secret,
        redirect_uri=f"{settings.web_origin}/api/v1/auth/callback",
        transport=transport,
    )


EngineDep = Annotated[AsyncEngine, Depends(get_engine)]
RedisDep = Annotated[Redis, Depends(get_redis)]
ClockDep = Annotated[Clock, Depends(get_clock)]
OidcDep = Annotated[OidcConfig, Depends(get_oidc)]


async def web_session(request: Request, engine: EngineDep, clock: ClockDep) -> WebSession:
    """The caller's live web session, or Unauthenticated: read from the database on every request."""
    session = await load_session(engine, request.cookies.get(COOKIE), clock)
    if session is None:
        raise Unauthenticated
    return session


SessionDep = Annotated[WebSession, Depends(web_session)]


async def get_current_principal(
    request: Request,
    engine: EngineDep,
    redis: RedisDep,
    clock: ClockDep,
    authorization: Annotated[str | None, Header()] = None,
) -> Principal:
    """The bearer-token caller, resolved from the database on every request; failures are counted per IP."""
    ip = request.client.host if request.client else "unknown"
    try:
        return (await resolve_bearer(engine, authorization)).principal
    except Unauthenticated:
        # Only a failing caller is throttled: a valid token never is, even behind a shared proxy IP.
        await auth_failed(redis, ip, clock)
        raise


PrincipalDep = Annotated[Principal, Depends(get_current_principal)]


async def get_actor(
    request: Request,
    engine: EngineDep,
    redis: RedisDep,
    clock: ClockDep,
    authorization: Annotated[str | None, Header()] = None,
) -> Principal:
    """A bearer token, else the web session's user in the organization it selected (role read now).

    Cookie-authenticated writes also need the web app's Origin; a bearer token carries no ambient cookie."""
    if authorization is not None:
        return await get_current_principal(request, engine, redis, clock, authorization)
    return await session_user(request, engine, clock)


async def session_user(request: Request, engine: EngineDep, clock: ClockDep) -> UserPrincipal:
    """The web session's user in its selected organization (role read now); writes need the web Origin."""
    session = await web_session(request, engine, clock)
    await require_same_origin(request)
    org = session.selected_organization_id
    role = await role_in(engine, session.user_id, org) if org else None
    if org is None or role is None:
        raise Forbidden("select an organization first")
    return UserPrincipal(session.user_id, org, Role[role.upper()])


ActorDep = Annotated[Principal, Depends(get_actor)]
# A person at the browser: no bearer token qualifies (minting credentials must not chain from a credential).
SessionUserDep = Annotated[UserPrincipal, Depends(session_user)]


def require_org_admin(actor: ActorDep) -> UserPrincipal:
    """Managing members, invitations and the org itself: a person with the org_admin role, never an agent."""
    if not isinstance(actor, UserPrincipal) or not can(actor.role, Action.MANAGE_ORG):
        raise Forbidden
    return actor


AdminDep = Annotated[UserPrincipal, Depends(require_org_admin)]


async def write_limit(actor: ActorDep, redis: RedisDep, clock: ClockDep) -> None:
    """Dependency for every write route (per principal and per organization)."""
    await check_write(redis, actor, clock)


async def user_write_limit(session: SessionDep, redis: RedisDep, clock: ClockDep) -> None:
    """Same, for writes made before an organization exists."""
    await check_user_write(redis, session.user_id, clock)


async def public_limit(request: Request, redis: RedisDep, clock: ClockDep) -> None:
    """Same, for a route anyone may call without a credential: counted per client IP."""
    await check_ip(redis, request.client.host if request.client else "unknown", clock)

from typing import Annotated

import httpx2
from fastapi import Depends, Header, Request
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncEngine

from app.adapters.clock import SystemClock
from app.api.ratelimit import auth_blocked, check_write, record_auth_failure
from app.authz.principals import Principal
from app.authz.resolver import resolve_bearer
from app.config import settings
from app.db.engine import make_engine
from app.domain.errors import LoginUnavailable, Unauthenticated
from app.ports.clock import Clock
from app.services.oidc import OidcConfig
from app.services.web_sessions import WebSession, load_session

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
    await auth_blocked(redis, ip, clock)
    try:
        return (await resolve_bearer(engine, authorization)).principal
    except Unauthenticated:
        await record_auth_failure(redis, ip, clock)
        raise


PrincipalDep = Annotated[Principal, Depends(get_current_principal)]


async def write_limit(principal: PrincipalDep, redis: RedisDep, clock: ClockDep) -> None:
    """Dependency for every write route (per principal and per organization)."""
    await check_write(redis, principal, clock)

import logging
import uuid
from datetime import datetime

from redis.asyncio import Redis
from redis.exceptions import RedisError

from app.authz.principals import AgentPrincipal, Principal
from app.config import settings
from app.domain.errors import RateLimited, RateLimiterUnavailable
from app.ports.clock import Clock

logger = logging.getLogger(__name__)
WINDOW = 60  # seconds; a fixed window, so a burst at the edge can reach twice the limit (ponytail)


def _slot(now: datetime) -> tuple[int, int]:
    """(window number, seconds until the next window)."""
    t = int(now.timestamp())
    return t // WINDOW, WINDOW - t % WINDOW


async def _hit(redis: Redis, key: str, now: datetime) -> tuple[int, int]:
    """Count one hit in this window: (hits so far, seconds to wait for the next window)."""
    window, wait = _slot(now)
    k = f"rl:{key}:{window}"
    try:
        async with redis.pipeline(transaction=True) as p:
            p.incr(k)
            p.expire(k, WINDOW * 2)
            hits = (await p.execute())[0]
    except (RedisError, OSError) as e:
        raise RateLimiterUnavailable from e
    return hits, wait


async def check_write(
    redis: Redis,
    principal: Principal,
    clock: Clock,
    per_principal: int | None = None,
    per_org: int | None = None,
) -> None:
    """Count one write against the principal's and the organization's budget; 429 when either is spent."""
    now, org = clock.now(), principal.organization_id
    pid = principal.agent_id if isinstance(principal, AgentPrincipal) else principal.user_id
    hits, wait = await _hit(redis, f"w:p:{org}:{pid}", now)
    if hits > (per_principal or settings.write_limit_per_principal):
        raise RateLimited(wait)
    hits, wait = await _hit(redis, f"w:o:{org}", now)
    if hits > (per_org or settings.write_limit_per_org):
        raise RateLimited(wait)


async def check_user_write(redis: Redis, user_id: uuid.UUID, clock: Clock) -> None:
    """Writes by a signed-in user who has no organization yet (create one, accept an invitation)."""
    hits, wait = await _hit(redis, f"w:u:{user_id}", clock.now())
    if hits > settings.write_limit_per_principal:
        raise RateLimited(wait)


async def check_ip(redis: Redis, ip: str, clock: Clock) -> None:
    """Unauthenticated writes (receipt verification), counted per client IP."""
    hits, wait = await _hit(redis, f"w:ip:{ip}", clock.now())
    if hits > settings.write_limit_per_principal:
        raise RateLimited(wait)


async def auth_failed(redis: Redis, ip: str, clock: Clock) -> None:
    """Count one failed bearer auth for this IP; 429 once it is over the limit (count and check are one INCR).
    Best effort: a Redis outage lets the 401 through (tokens are 256-bit; an outage must not break reads)."""
    try:
        hits, wait = await _hit(redis, f"af:{ip}", clock.now())
    except RateLimiterUnavailable:
        logger.warning("auth-failure limiter unavailable")
        return
    if hits > settings.auth_failures_per_minute:
        raise RateLimited(wait)

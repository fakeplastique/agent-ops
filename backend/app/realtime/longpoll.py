import asyncio
import logging
import math
import time
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from typing import Any

from redis.asyncio import Redis
from redis.exceptions import RedisError
from sqlalchemy.ext.asyncio import AsyncEngine

from app.authz.matrix import Role
from app.domain.errors import RateLimited, RateLimiterUnavailable
from app.realtime.feed import read_after
from app.realtime.projection import project

logger = logging.getLogger(__name__)
MAX_WAIT = 25  # seconds; the route refuses more
TICK = 0.5  # re-query interval while waiting (S29-D1: no transaction is held across a sleep)
MAX_WAITERS = 5  # concurrent polls per user and organization (S29-D5)
_SLOT_TTL = MAX_WAIT + 5  # a slot outlives its poll's longest wait, then frees itself


def waiter_key(org: uuid.UUID, user: uuid.UUID) -> str:
    return f"lp:{org}:{user}"


@asynccontextmanager
async def waiter_slot(
    redis: Redis, org: uuid.UUID, user: uuid.UUID, now: float
) -> AsyncIterator[None]:
    """Hold one of the user's MAX_WAITERS slots (a Redis sorted set shared by every instance) for one poll.

    Each slot is scored by its own expiry, so a crashed holder's slot drops out by itself. Over the cap is
    429 with Retry-After; a limiter outage is 503 (fails closed like the write limiter)."""
    key, slot = waiter_key(org, user), uuid.uuid4().hex
    try:
        async with redis.pipeline(transaction=True) as p:
            p.zremrangebyscore(key, "-inf", now)
            p.zadd(key, {slot: now + _SLOT_TTL})
            p.zcard(key)
            p.zrange(key, 0, 0, withscores=True)
            p.expire(key, _SLOT_TTL)
            _, _, count, oldest, _ = await p.execute()
    except (RedisError, OSError) as e:
        raise RateLimiterUnavailable from e
    try:
        if count > MAX_WAITERS:
            raise RateLimited(min(_SLOT_TTL, math.ceil(oldest[0][1] - now)))
        yield
    finally:
        try:
            await redis.zrem(key, slot)
        except (RedisError, OSError):
            logger.warning("waiter slot not released; it expires by itself")


async def poll(
    engine: AsyncEngine,
    org: uuid.UUID,
    after: int,
    wait: float,
    limit: int,
    recheck: Callable[[], Awaitable[Role]],
    gone: Callable[[], Awaitable[bool]],
) -> tuple[list[dict[str, Any]], int]:
    """Events after the cursor, waiting up to `wait` seconds for the first one; returns (events, next cursor).

    Access is re-checked right before a batch leaves (`recheck` raises when it lapsed, else gives the role
    read now), so a member removed mid-wait gets nothing. Dropped events still move the cursor. The wait is
    wall time by design (S29-D6); it ends early once the client is `gone`."""
    deadline = time.monotonic() + min(wait, MAX_WAIT)
    while True:
        rows = await read_after(engine, org, after, limit)
        if rows:
            role = await recheck()
            events = []
            for r in rows:
                data = project(r["type"], r["payload"], role)
                if data is not None:
                    keep = ("id", "seq", "type", "created_at")
                    events.append({k: r[k] for k in keep} | {"data": data})
            return events, rows[-1]["seq"]
        remaining = deadline - time.monotonic()
        if remaining <= 0 or await gone():
            return [], after
        await asyncio.sleep(min(TICK, remaining))

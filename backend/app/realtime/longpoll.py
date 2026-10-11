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
# A slot outlives its slowest poll, then frees itself: after the last deadline check a poll can still wait
# on three pool checkouts (one read, two to re-resolve the credential; SQLAlchemy's default 30 s each).
_POOL_TIMEOUT = 30
_SLOT_TTL = MAX_WAIT + 3 * _POOL_TIMEOUT + 5
_releasing: set[asyncio.Task[None]] = set()  # strong refs: the loop keeps only weak ones


def waiter_key(org: uuid.UUID, user: uuid.UUID) -> str:
    return f"lp:{org}:{user}"


async def _release(redis: Redis, key: str, slot: str) -> None:
    try:
        await asyncio.wait_for(redis.zrem(key, slot), 2)
    except (RedisError, OSError, TimeoutError):
        logger.warning("waiter slot not released; it expires by itself")


@asynccontextmanager
async def waiter_slot(
    redis: Redis, org: uuid.UUID, user: uuid.UUID, now: float, ttl: float = _SLOT_TTL
) -> AsyncIterator[None]:
    """Hold one of the user's MAX_WAITERS slots (a Redis sorted set shared by every instance) for one poll
    or one WebSocket (both transports draw from the same cap; `ttl` outlives the holder).

    Each slot is scored by its own expiry, so a crashed holder's slot drops out by itself. Over the cap is
    429 with Retry-After; a limiter outage is 503 (fails closed like the write limiter)."""
    key, slot = waiter_key(org, user), uuid.uuid4().hex
    try:
        try:
            async with redis.pipeline(transaction=True) as p:
                p.zremrangebyscore(key, "-inf", now)
                p.zadd(key, {slot: now + ttl})
                p.zcard(key)
                p.zrange(key, 0, 0, withscores=True)
                # The key lives as long as its longest slot: a short holder never cuts a long one's expiry.
                p.expire(key, math.ceil(ttl), nx=True)
                p.expire(key, math.ceil(ttl), gt=True)
                _, _, count, oldest, _, _ = await p.execute()
        except (RedisError, OSError) as e:
            raise RateLimiterUnavailable from e
        if count > MAX_WAITERS:
            raise RateLimited(min(math.ceil(ttl), math.ceil(oldest[0][1] - now)))
        yield
    finally:
        # Its own task, shielded: a cancelled handler (anyio cancels again at every await) still releases,
        # and so does a cancel that lands after the pipeline was applied.
        task = asyncio.ensure_future(_release(redis, key, slot))
        _releasing.add(task)
        task.add_done_callback(_releasing.discard)
        await asyncio.shield(task)


def project_rows(rows: list[dict[str, Any]], role: Role) -> list[dict[str, Any]]:
    """The events of a batch this role may read, each cut to its allow-list (long-poll and WebSocket)."""
    events = []
    for r in rows:
        data = project(r["type"], r["payload"], role)
        if data is not None:
            keep = ("id", "seq", "type", "created_at")
            events.append({k: r[k] for k in keep} | {"data": data})
    return events


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
            return project_rows(rows, await recheck()), rows[-1]["seq"]
        remaining = deadline - time.monotonic()
        if remaining <= 0 or await gone():
            return [], after
        await asyncio.sleep(min(TICK, remaining))

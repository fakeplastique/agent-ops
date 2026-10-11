import asyncio
import logging
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable, Iterable
from contextlib import asynccontextmanager

import anyio
from redis.asyncio import Redis
from redis.exceptions import RedisError

logger = logging.getLogger(__name__)

Wait = Callable[[float], Awaitable[None]]


def channel(org: uuid.UUID) -> str:
    return f"rt:{org}"


async def publish(redis: Redis, orgs: Iterable[uuid.UUID]) -> None:
    """Wake the sockets of these organizations. A hint only: the outbox is the source, a lost one costs a
    poll interval, so a Redis error is logged and never fails the caller."""
    try:
        for org in orgs:
            await redis.publish(channel(org), b"")
    except (RedisError, OSError):
        logger.warning("realtime wake-up not published; sockets fall back to polling")


@asynccontextmanager
async def subscription(redis: Redis, org: uuid.UUID) -> AsyncIterator[Wait]:
    """A `wait(timeout)` that returns at the next wake-up for this organization or at the timeout.

    A broken subscription only sleeps out the timeout (redis-py reconnects and re-subscribes on the next
    read), so the caller degrades to polling instead of failing."""
    ps = redis.pubsub()

    async def wait(timeout: float) -> None:
        deadline = asyncio.get_running_loop().time() + timeout
        try:
            # Only a published message wakes; a (re)subscribe confirmation does not.
            while (left := deadline - asyncio.get_running_loop().time()) > 0:
                msg = await ps.get_message(timeout=left)
                if msg is not None and msg["type"] == "message":
                    return
        except (RedisError, OSError, RuntimeError):  # RuntimeError: never subscribed
            await asyncio.sleep(max(0.0, deadline - asyncio.get_running_loop().time()))

    try:
        try:
            await ps.subscribe(channel(org))
        except (RedisError, OSError):
            logger.warning("realtime subscribe failed; polling only")
        yield wait
    finally:
        # Shielded and bounded: a cancelled socket still hands its Redis connection back.
        with anyio.move_on_after(2, shield=True):
            try:
                await ps.aclose()
            except (RedisError, OSError):
                logger.warning("realtime subscription not closed cleanly")

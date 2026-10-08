import uuid

from redis.asyncio import Redis
from redis.asyncio.retry import Retry
from redis.backoff import NoBackoff

from app.services.cache import REDIS_TIMEOUT


class RedisCache:
    """CacheStore on Redis. The stamp is random, never a counter: if Redis evicts it, the replacement
    cannot equal an older stamp, so entries written under that one stay unreachable."""

    def __init__(self, url: str) -> None:
        # No retries: the timeout is the whole cost of an unreachable cache, whatever redis-py defaults to.
        self.redis = Redis.from_url(
            url,
            socket_timeout=REDIS_TIMEOUT,
            socket_connect_timeout=REDIS_TIMEOUT,
            retry=Retry(NoBackoff(), 0),
            decode_responses=True,
        )

    async def stamp(self, org: uuid.UUID) -> str:
        fresh = uuid.uuid4().hex
        # One round trip: the existing stamp, or ours if there was none.
        return await self.redis.set(f"cache:{org}:stamp", fresh, nx=True, get=True) or fresh

    async def bump(self, org: uuid.UUID) -> None:
        await self.redis.set(f"cache:{org}:stamp", uuid.uuid4().hex)

    async def get(self, key: str) -> str | None:
        return await self.redis.get(key)

    async def set(self, key: str, value: str, ttl: int) -> None:
        await self.redis.set(key, value, ex=ttl)

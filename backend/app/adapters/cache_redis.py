import uuid

from redis.asyncio import Redis

TIMEOUT = 0.25  # seconds: an unreachable cache costs a moment, then the database answers


class RedisCache:
    """CacheStore on Redis. The stamp is random, never a counter: if Redis evicts it, the replacement
    cannot equal an older stamp, so entries written under that one stay unreachable."""

    def __init__(self, url: str) -> None:
        self.redis = Redis.from_url(
            url, socket_timeout=TIMEOUT, socket_connect_timeout=TIMEOUT, decode_responses=True
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

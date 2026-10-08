"""Read-through cache for per-organization decisions (spec 5.6, D27): flags, policy resolution, stats.

Keys are `cache:{org}:{stamp}:{name}:{args}`. A write replaces the org's stamp after its commit, so every
entry of that org becomes unreachable at once, and a reader that loaded before the write stores its value
under the stamp it read first: a dead key, never a resurrected one. Any cache failure falls through to the
database. With no store configured (tests, scripts) every read goes to the database."""

import json
import time
import uuid
from collections import Counter
from collections.abc import Awaitable, Callable
from typing import Any, Protocol

from sqlalchemy.ext.asyncio import AsyncSession

TTL = 60  # seconds; bounds staleness only where invalidation itself failed in another process


class CacheStore(Protocol):
    async def stamp(self, org: uuid.UUID) -> str: ...
    async def bump(self, org: uuid.UUID) -> None: ...
    async def get(self, key: str) -> str | None: ...
    async def set(self, key: str, value: str, ttl: int) -> None: ...


HITS: Counter[str] = Counter()
MISSES: Counter[str] = Counter()
ERRORS: Counter[str] = Counter()
# org -> monotonic deadline: after a failed bump this process reads that org from the database for a TTL.
BYPASS: dict[uuid.UUID, float] = {}
_store: CacheStore | None = None


def configure(store: CacheStore | None) -> None:
    global _store
    _store = store


def _dirty(session: AsyncSession | None, org: uuid.UUID) -> bool:
    return session is not None and org in session.info.get("cache_dirty", ())


async def get_or_load(
    session: AsyncSession | None,
    org: uuid.UUID,
    name: str,
    args: str,
    load: Callable[[], Awaitable[Any]],
) -> Any:
    """The cached JSON value of `load()` for this org; `session` is the caller's transaction, if any."""
    store = _store
    # A transaction that has written this org must read its own writes, not entries from before them.
    if store is None or _dirty(session, org) or BYPASS.get(org, 0) > time.monotonic():
        return await load()
    try:
        key = f"cache:{org}:{await store.stamp(org)}:{name}:{args}"  # stamp read before the load
        raw = await store.get(key)
    except Exception:  # noqa: BLE001 - any cache failure falls through to the database
        ERRORS[name] += 1
        return await load()
    if raw is not None:
        HITS[name] += 1
        return json.loads(raw)
    MISSES[name] += 1
    value = await load()
    try:
        await store.set(key, json.dumps(value), TTL)
    except Exception:  # noqa: BLE001
        ERRORS[name] += 1
    return value


def invalidate(session: AsyncSession, org: uuid.UUID) -> None:
    """Drop the org's entries once this transaction commits (tenant_session runs the hook)."""
    dirty: set[uuid.UUID] = session.info.setdefault("cache_dirty", set())
    if org not in dirty:
        dirty.add(org)
        session.info.setdefault("after_commit", []).append(lambda: invalidate_now(org))


async def invalidate_now(org: uuid.UUID) -> None:
    """For writes already committed (engine transactions, an operator's SQL edit)."""
    if _store is None:
        return
    try:
        await _store.bump(org)
    except Exception:  # noqa: BLE001 - this process stops trusting the cache; others wait out the TTL
        ERRORS["invalidate"] += 1
        BYPASS[org] = time.monotonic() + TTL


def exposition() -> str:
    """Prometheus text: counters by cache name only, never a tenant id."""
    lines = []
    for metric, counts in (("hit", HITS), ("miss", MISSES), ("error", ERRORS)):
        lines.append(f"# TYPE cache_{metric}_total counter")
        lines += [f'cache_{metric}_total{{cache="{n}"}} {v}' for n, v in sorted(counts.items())]
    return "\n".join(lines) + "\n"

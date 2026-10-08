"""Read-through cache for per-organization decisions (spec 5.6, D27): flags, policy resolution, stats.

Keys are `cache:{org}:{stamp}:{name}:{args}`. A write replaces the org's stamp after its commit, so every
entry of that org becomes unreachable at once, and a reader that loaded before the write stores its value
under the stamp it read first: a dead key, never a resurrected one. Any cache failure falls through to the
database. With no store configured (tests, scripts) every read goes to the database."""

import asyncio
import json
import time
import uuid
from collections import Counter
from collections.abc import Awaitable, Callable
from typing import Any, Protocol

from sqlalchemy.ext.asyncio import AsyncSession

TTL = 60  # seconds; bounds staleness only where invalidation itself failed in another process
REDIS_TIMEOUT = 0.25  # seconds per Redis call (the adapter's socket timeouts)
BREAK = 5  # seconds the cache is skipped after an error: in an outage only the first call waits
monotonic = time.monotonic  # tests move it


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
IN_FLIGHT: set[asyncio.Task[None]] = set()  # stamp bumps, referenced so nothing can drop them
_store: CacheStore | None = None
_broken_until = 0.0


def reset() -> None:
    global _broken_until
    BYPASS.clear()
    _broken_until = 0.0


def _broke() -> None:
    global _broken_until
    _broken_until = monotonic() + BREAK


def configure(store: CacheStore | None) -> None:
    global _store
    _store = store


def _bypass(session: AsyncSession | None, org: uuid.UUID) -> bool:
    if session is None:
        return False
    # Entries skip RLS: only a session scoped to this org may read them, and not after it wrote to it.
    info = session.info
    return info.get("organization_id") != org or org in info.get("cache_dirty", ())


async def get_or_load(
    session: AsyncSession | None,
    org: uuid.UUID,
    name: str,
    args: str,
    load: Callable[[], Awaitable[Any]],
) -> Any:
    """The cached JSON value of `load()` for this org; `session` is the caller's transaction, if any."""
    store = _store
    now = monotonic()
    skip = now < _broken_until or BYPASS.get(org, 0) > now
    if store is None or skip or _bypass(session, org):
        return await load()
    try:
        key = f"cache:{org}:{await store.stamp(org)}:{name}:{args}"  # stamp read before the load
        raw = await store.get(key)
    except Exception:  # noqa: BLE001 - any cache failure falls through to the database
        ERRORS[name] += 1
        _broke()
        return await load()
    if raw is not None:
        HITS[name] += 1
        return json.loads(raw)
    MISSES[name] += 1
    raw = json.dumps(await load())
    try:
        await store.set(key, raw, TTL)
    except Exception:  # noqa: BLE001
        ERRORS[name] += 1
        _broke()
    return json.loads(raw)  # the same types a hit returns


def invalidate(session: AsyncSession, org: uuid.UUID) -> None:
    """Drop the org's entries once this transaction commits (tenant_session runs the hook)."""
    dirty: set[uuid.UUID] = session.info.setdefault("cache_dirty", set())
    if org not in dirty:
        dirty.add(org)
        session.info.setdefault("after_commit", []).append(lambda: start_invalidation(org))


def start_invalidation(org: uuid.UUID) -> Awaitable[None] | None:
    """Synchronous before any await, so a cancelled request still leaves this process safe: the org is read
    from the database until the bump lands. The bump runs as its own task, so cancellation cannot drop it."""
    store = _store
    if store is None:
        return None
    deadline = BYPASS[org] = monotonic() + TTL
    task = asyncio.ensure_future(_bump(store, org, deadline))
    IN_FLIGHT.add(task)
    task.add_done_callback(IN_FLIGHT.discard)
    return asyncio.shield(task)


async def _bump(store: CacheStore, org: uuid.UUID, deadline: float) -> None:
    try:
        await store.bump(org)
    except Exception:  # noqa: BLE001 - the bypass stays; other processes wait out the TTL
        ERRORS["invalidate"] += 1  # no breaker: reads trip it themselves if Redis is really down
        return
    if BYPASS.get(org) == deadline:  # a later invalidation still in flight keeps its own mark
        del BYPASS[org]


async def invalidate_now(org: uuid.UUID) -> None:
    """For writes already committed (engine transactions, an operator's SQL edit)."""
    if (pending := start_invalidation(org)) is not None:
        await pending


def exposition() -> str:
    """Prometheus text: counters by cache name only, never a tenant id."""
    lines = []
    for metric, counts in (("hit", HITS), ("miss", MISSES), ("error", ERRORS)):
        lines.append(f"# TYPE cache_{metric}_total counter")
        lines += [f'cache_{metric}_total{{cache="{n}"}} {v}' for n, v in sorted(counts.items())]
    return "\n".join(lines) + "\n"

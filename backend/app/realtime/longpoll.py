import asyncio
import time
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

from sqlalchemy.ext.asyncio import AsyncEngine

from app.authz.matrix import Role
from app.realtime.feed import read_after
from app.realtime.projection import project

MAX_WAIT = 25  # seconds; the route refuses more
TICK = 0.5  # re-query interval while waiting (S29-D1: no transaction is held across a sleep)


async def poll(
    engine: AsyncEngine,
    org: uuid.UUID,
    after: int,
    wait: float,
    limit: int,
    recheck: Callable[[], Awaitable[Role]],
) -> tuple[list[dict[str, Any]], int]:
    """Events after the cursor, waiting up to `wait` seconds for the first one; returns (events, next cursor).

    Access is re-checked right before a batch leaves (`recheck` raises when it lapsed, else gives the role
    read now), so a member removed mid-wait gets nothing. Dropped events still move the cursor."""
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
        if remaining <= 0:
            return [], after
        await asyncio.sleep(min(TICK, remaining))

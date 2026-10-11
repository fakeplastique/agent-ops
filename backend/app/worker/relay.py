import logging
import uuid
from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import Any, Protocol

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.adapters.clock import SystemClock
from app.ports.clock import Clock
from app.worker.registry import CONCERNS, job_id

log = logging.getLogger(__name__)


class Enqueuer(Protocol):
    async def enqueue_job(
        self, function: str, *args: Any, _job_id: str | None = None, **kw: Any
    ) -> Any: ...


# app_worker sees ids only. SKIP LOCKED: a second relay takes other rows; order is by tenant then seq, so one
# tenant's events enter the queue in order (consumers still order by seq across relays).
_PICK = text(
    "select id, organization_id from outbox_events where published_at is null"
    " order by organization_id, seq limit :n for update skip locked"
)
_MARK = text("update outbox_events set published_at = :now where id = any(:ids) returning id")


async def relay_once(
    worker_engine: AsyncEngine,
    queue: Enqueuer,
    clock: Clock | None = None,
    *,
    batch: int = 100,
    notify: Callable[[set[uuid.UUID]], Awaitable[None]] | None = None,
) -> dict[str, int]:
    """Publish every unpublished event: enqueue one job per concern, then stamp the batch published.

    Enqueue comes first and the stamp commits with it or not at all: a relay killed midway, or a Redis that is
    down, leaves the events unpublished and the next run sends them again (arq drops the duplicate job ids).
    Redis loss delays events, never loses them."""
    clock = clock or SystemClock()
    published = errors = 0
    while True:
        try:
            async with worker_engine.begin() as conn:
                rows = (await conn.execute(_PICK, {"n": batch})).all()
                if not rows:
                    break
                for event_id, org in rows:
                    for concern in CONCERNS:
                        await queue.enqueue_job(
                            concern, str(org), str(event_id), _job_id=job_id(event_id, concern)
                        )
                now: datetime = clock.now()
                stamped = (
                    await conn.execute(_MARK, {"now": now, "ids": [r[0] for r in rows]})
                ).all()
                if len(stamped) != len(rows):  # else the same rows would be picked again, forever
                    raise RuntimeError(f"stamped {len(stamped)} of {len(rows)} outbox events")
            published += len(rows)
            if notify is not None:  # after commit: a socket woken now reads the batch
                await notify({org for _, org in rows})
        except (
            Exception
        ):  # Redis or the database is away: stop, the batch rolled back, the next run retries
            log.exception("outbox relay batch failed")
            errors += 1
            break
    return {"published": published, "errors": errors}

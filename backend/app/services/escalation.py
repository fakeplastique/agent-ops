import logging
import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.db.session import tenant_session
from app.domain.statuses import Status
from app.domain.transitions import Event, transition
from app.ports.clock import Clock
from app.services import audit, outbox
from app.services.status import change_status
from app.services.steps import open_first_step, open_step
from app.services.votes import reveal_round_one, round_votes

log = logging.getLogger(__name__)

# The scan runs as app_worker and sees ids only (migration 0007); everything below runs as app_rt in the
# item's own tenant session. A stale candidate is harmless: each item is re-checked under the request lock.
_DUE_STEPS = text(
    "select organization_id, id, due_at as at from approval_steps where status = 'pending' and due_at <= :now"
    " and (due_at, id) > (:after_at, :after_id) order by due_at, id limit :n"
)
_DUE_REQUESTS = text(
    "select organization_id, id, voting_deadline_at as at from decision_requests"
    " where status in ('pending_votes', 'pending_discussion') and voting_deadline_at <= :now"
    " and (voting_deadline_at, id) > (:after_at, :after_id) order by voting_deadline_at, id limit :n"
)
_STEP_REQUEST = text(
    "select decision_request_id from approval_steps where organization_id = :o and id = :s"
)
_STEP = text(
    "select id, request_version, level, status from approval_steps where organization_id = :o and id = :s"
)
# SKIP LOCKED: a human deciding, a vote or another worker holds the request: leave it, next minute retries.
_LOCK = text(
    "select id, status, version, policy_snapshot, voting_deadline_at from decision_requests"
    " where organization_id = :o and id = :r for update skip locked"
)
_TIME_OUT = text(
    "update approval_steps set status = 'timed_out' where organization_id = :o and id = :s"
    " and status = 'pending' returning id"
)
_REASON = text(
    "update decision_requests set escalation_reason = 'quorum_not_reached' where organization_id = :o and id = :r"
)

Handler = Callable[[AsyncSession, uuid.UUID, uuid.UUID, datetime], Awaitable[bool]]


async def escalate_step(
    session: AsyncSession, o: uuid.UUID, step_id: uuid.UUID, now: datetime
) -> bool:
    """An overdue pending step times out once: the next level opens as a stand-in, the last level expires the
    request. Never approves. Returns whether it did anything."""
    request_id = (await session.execute(_STEP_REQUEST, {"o": o, "s": step_id})).scalar()
    if request_id is None:
        return False
    req = (await session.execute(_LOCK, {"o": o, "r": request_id})).mappings().first()
    if req is None:
        return False
    step = (await session.execute(_STEP, {"o": o, "s": step_id})).mappings().one()
    if (
        step["status"] != "pending"
        or step["request_version"] != req["version"]
        or req["status"] != Status.PENDING_APPROVAL.value
    ):
        return False
    if (await session.execute(_TIME_OUT, {"o": o, "s": step_id})).first() is None:
        return False
    data = {
        "decision_request_id": str(request_id),
        "request_version": req["version"],
        "level": step["level"],
    }
    await audit.record(session, o, "approval_step", step_id, "timed_out", "system", None, data)
    await outbox.emit(session, o, "step.timed_out", data | {"id": str(step_id)})
    snapshot = req["policy_snapshot"]
    if step["level"] >= len(snapshot["levels"]):
        old = Status.PENDING_APPROVAL
        new = transition(old, Event.LAST_STEP_TIMED_OUT)
        await change_status(
            session, o, request_id, req["version"], old, new, now, None, "system", None
        )
    else:
        await open_step(
            session, o, request_id, req["version"], step["level"] + 1, snapshot, now, step["level"]
        )
    return True


async def escalate_voting(
    session: AsyncSession, o: uuid.UUID, request_id: uuid.UUID, now: datetime
) -> bool:
    """A request whose voting deadline has passed goes to humans with `quorum_not_reached`. Never approves,
    never expires silently. Round 1 is revealed: no vote can arrive any more."""
    req = (await session.execute(_LOCK, {"o": o, "r": request_id})).mappings().first()
    if req is None:
        return False
    old = Status(req["status"])
    if (
        old not in (Status.PENDING_VOTES, Status.PENDING_DISCUSSION)
        or req["voting_deadline_at"] > now
    ):
        return False
    version, snapshot = req["version"], req["policy_snapshot"]
    if old is Status.PENDING_VOTES:
        await reveal_round_one(
            session,
            o,
            request_id,
            version,
            now,
            await round_votes(session, o, request_id, version, 1),
        )
    new = transition(old, Event.DEADLINE)
    await change_status(session, o, request_id, version, old, new, now, None, "system", None)
    await session.execute(_REASON, {"o": o, "r": request_id})
    data: dict[str, Any] = {
        "reason": "quorum_not_reached",
        "from": old.value,
        "request_version": version,
    }
    await audit.record(
        session, o, "decision_request", request_id, "escalated", "system", None, data
    )
    await open_first_step(session, o, request_id, version, snapshot, now)
    return True


async def _sweep(
    scan: AsyncEngine, work: AsyncEngine, query: Any, handler: Handler, now: datetime, limit: int,
    counts: dict[str, int], key: str,
) -> None:  # fmt: skip
    """Walk every due item in pages after a (time, id) cursor: an item that is skipped (locked) or keeps
    failing stays due, but the cursor moves past it, so it cannot hold a slot and starve what follows."""
    after_at, after_id = datetime.min.replace(tzinfo=UTC), uuid.UUID(int=0)
    while True:
        async with scan.connect() as conn:
            result = await conn.execute(
                query, {"now": now, "n": limit, "after_at": after_at, "after_id": after_id}
            )
            page: list[tuple[uuid.UUID, uuid.UUID, datetime]] = [
                (r["organization_id"], r["id"], r["at"]) for r in result.mappings()
            ]
        if not page:
            return
        for org, item, _ in page:
            try:
                async with tenant_session(
                    work, org
                ) as session:  # the tenant comes from the item, never the worker
                    if await handler(session, org, item, now):
                        counts[key] += 1
            except Exception:  # one poison item must not stall the rest of the sweep
                log.exception("timeout handling failed for %s %s", key, item)
                counts["errors"] += 1
        _, after_id, after_at = page[-1]


async def run_timeouts(
    scan_engine: AsyncEngine, work_engine: AsyncEngine, clock: Clock, *, limit: int = 100
) -> dict[str, int]:
    """One sweep: overdue steps, then requests past their voting deadline, `limit` ids per page. Safe to repeat
    and to run from two workers at once (each item is re-checked under the request lock, skipped if someone
    holds it). The only clock is `clock`."""
    now = clock.now()
    counts = {"steps": 0, "requests": 0, "errors": 0}
    await _sweep(scan_engine, work_engine, _DUE_STEPS, escalate_step, now, limit, counts, "steps")
    await _sweep(
        scan_engine, work_engine, _DUE_REQUESTS, escalate_voting, now, limit, counts, "requests"
    )
    return counts

"""Webhook delivery (S28, D18): at least once, signed per Standard Webhooks, bounded by one deadline per call.

Each delivery row is the `webhook-id`: one per (subscription, event), the same across retries and redelivers.
DB work happens in short tenant sessions as app_rt; the HTTP call runs between them, never inside one."""

import asyncio
import json
import logging
import uuid
from collections.abc import Awaitable, Callable, Mapping
from datetime import datetime, timedelta
from typing import Any

import httpx2
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.db.session import tenant_session
from app.domain.errors import WebhookUrlRefused
from app.ports.clock import Clock
from app.services import webhooks as subscriptions
from app.services.webhook_security import post_pinned, signed_headers

log = logging.getLogger(__name__)

type Send = Callable[[str, bytes, Mapping[str, str]], Awaitable[int]]

MAX_ATTEMPTS = 5
# Wait after failed attempt n (1-4). After the 5th the row is dead; its lease only covers a lost worker.
BACKOFF = (30, 120, 900, 3600)
LEASE = 60  # seconds: longer than DNS (5 s) plus the call (5 s)

_FAN_OUT = text(
    "insert into webhook_deliveries (organization_id, subscription_id, event_id, event_type, next_attempt_at)"
    " select :o, s.id, e.id, e.type, :now from webhook_subscriptions s join outbox_events e"
    "  on e.organization_id = s.organization_id and e.id = :e"
    " where s.organization_id = :o and s.active and e.type = any(s.events)"
    " on conflict (subscription_id, event_id) do nothing returning id"
)
# SKIP LOCKED: another worker is sending this row right now; it will record the result.
_CLAIM = text(
    "select d.status, d.attempt, d.next_attempt_at, s.url, s.active, s.id as subscription_id,"
    " e.type, e.payload, e.seq, e.created_at"
    " from webhook_deliveries d join webhook_subscriptions s on s.id = d.subscription_id"
    " join outbox_events e on e.id = d.event_id"
    " where d.organization_id = :o and d.id = :d for update of d skip locked"
)
_START = text(
    "update webhook_deliveries set attempt = attempt + 1, next_attempt_at = :next, updated_at = now()"
    " where organization_id = :o and id = :d returning attempt"
)
# Conditional on the attempt it belongs to: a stale result never overwrites a newer attempt or a redeliver.
_FINISH = text(
    "update webhook_deliveries set status = :st, next_attempt_at = case when :st = 'pending'"
    "  then next_attempt_at end, last_status_code = :code, last_error = :err, updated_at = now()"
    " where organization_id = :o and id = :d and attempt = :n and status = 'pending'"
)
_KILL = text(
    "update webhook_deliveries set status = 'dead', next_attempt_at = null, last_error = :err,"
    " updated_at = now() where organization_id = :o and id = :d"
)


def _wait(attempt: int) -> timedelta:
    return timedelta(seconds=BACKOFF[attempt - 1] if attempt <= len(BACKOFF) else LEASE)


def body(type: str, payload: dict[str, Any], seq: int, at: datetime) -> bytes:
    data = dict(payload)
    return json.dumps(
        {"type": type, "timestamp": at.isoformat(), "seq": seq, "data": data},
        separators=(",", ":"),
    ).encode()


async def _claim(
    engine: AsyncEngine, org: uuid.UUID, delivery: uuid.UUID, now: datetime
) -> tuple[int, str, bytes, str] | None:
    """Take the next attempt of a due row: (attempt, url, body, secret), or None when there is nothing to send."""
    async with tenant_session(engine, org) as s:
        row = (await s.execute(_CLAIM, {"o": org, "d": delivery})).mappings().first()
        if row is None or row["status"] != "pending" or row["next_attempt_at"] > now:
            return None
        if row["attempt"] >= MAX_ATTEMPTS or not row["active"]:
            err = "attempts exhausted" if row["active"] else "subscription is inactive"
            await s.execute(_KILL, {"o": org, "d": delivery, "err": err})
            return None
        n = row["attempt"] + 1
        await s.execute(_START, {"o": org, "d": delivery, "next": now + _wait(n)})
        secret = await subscriptions.load_secret(s, org, row["subscription_id"])
        return (
            n,
            row["url"],
            body(row["type"], row["payload"], row["seq"], row["created_at"]),
            secret,
        )


async def deliver(
    engine: AsyncEngine, org: uuid.UUID, delivery: uuid.UUID, clock: Clock, send: Send = post_pinned
) -> str | None:
    """One attempt at one delivery row. Returns the row's new status, or None if nothing was due."""
    now = clock.now()
    claimed = await _claim(engine, org, delivery, now)
    if claimed is None:
        return None
    n, url, payload, secret = claimed
    headers = signed_headers(secret, str(delivery), int(now.timestamp()), payload)
    code: int | None = None
    try:
        code = await send(url, payload, headers)
        err = None if 200 <= code < 300 else f"HTTP {code}"
    except WebhookUrlRefused as e:
        err = f"refused: {e.details['rule']}"
    except TimeoutError:
        err = "timeout"
    except (httpx2.HTTPError, OSError) as e:
        err = f"connection failed: {type(e).__name__}"
    status = "delivered" if err is None else "dead" if n >= MAX_ATTEMPTS else "pending"
    params = {"o": org, "d": delivery, "n": n, "st": status, "code": code, "err": err}
    async with tenant_session(engine, org) as s:
        await s.execute(_FINISH, params)
    return status


async def _each(
    engine: AsyncEngine, items: list[tuple[uuid.UUID, uuid.UUID]], clock: Clock, send: Send
) -> None:
    # Side by side: a receiver that hangs costs its own 5 s, never the others'.
    results = await asyncio.gather(
        *(deliver(engine, org, d, clock, send) for org, d in items), return_exceptions=True
    )
    for r in results:
        if isinstance(r, BaseException):
            log.error("webhook delivery failed", exc_info=r)


async def fan_out(
    engine: AsyncEngine, org: uuid.UUID, event: uuid.UUID, clock: Clock, send: Send = post_pinned
) -> int:
    """One row per active subscription that wants this event, then the first attempt of each.

    Idempotent on (event, concern): a re-run inserts no row, so it sends nothing; a row left behind by a
    worker that died before sending is still pending and the retry sweep takes it."""
    async with tenant_session(engine, org) as s:
        rows = (await s.execute(_FAN_OUT, {"o": org, "e": event, "now": clock.now()})).all()
    await _each(engine, [(org, r[0]) for r in rows], clock, send)
    return len(rows)


async def webhooks(ctx: dict[str, Any], organization_id: str, event_id: str) -> int:
    """The arq consumer the relay enqueues as `webhooks` (registry.py)."""
    return await fan_out(
        ctx["work_engine"], uuid.UUID(organization_id), uuid.UUID(event_id), ctx["clock"]
    )

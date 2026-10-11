"""The `notifications` consumer (S31): approval prompts on Telegram when a step opens, the outcome when it
leaves pending. Bot API calls run between tenant sessions, never inside one."""

import asyncio
import logging
import uuid
from typing import Any

from arq.worker import Retry
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.db.session import tenant_session
from app.ports.notifier import Notifier
from app.services import telegram

log = logging.getLogger(__name__)

_EVENT = text("select type, payload from outbox_events where organization_id = :o and id = :e")
_OUTCOMES = frozenset({"step.decided", "step.timed_out"})


async def _send(notifier: Notifier, p: telegram.Prompt) -> int | None:
    try:
        return await notifier.send(p.chat_id, p.text, p.buttons)
    except Exception as e:  # noqa: BLE001 - one chat failing (or timing out) never holds up the rest
        log.warning("telegram send failed: %s", type(e).__name__)
        return None


async def _edit(notifier: Notifier, chat: int, message: int, body: str) -> bool:
    try:
        await notifier.edit(chat, message, body)
        return True
    except Exception as e:  # noqa: BLE001
        log.warning("telegram edit failed: %s", type(e).__name__)
        return False


async def notify(engine: AsyncEngine, org: uuid.UUID, event: uuid.UUID, notifier: Notifier) -> int:
    """Handle one outbox event; returns how many Bot API calls succeeded."""
    async with tenant_session(engine, org) as s:
        row = (await s.execute(_EVENT, {"o": org, "e": event})).first()
        if row is None or not isinstance(row.payload, dict) or "id" not in row.payload:
            return 0
        step = uuid.UUID(row.payload["id"])
        if row.type == "step.created":
            prompts, edits = await telegram.prompts(s, org, step), []
        elif row.type in _OUTCOMES:
            prompts, edits = [], await telegram.shown(s, org, step)
        else:
            return 0
    # Side by side, each bounded by the adapter's 10 s deadline: a hanging chat costs only itself.
    sent = await asyncio.gather(*(_send(notifier, p) for p in prompts))
    edited = await asyncio.gather(*(_edit(notifier, *e) for e in edits))
    done = [(p.row_id, mid) for p, mid in zip(prompts, sent, strict=True) if mid is not None]
    if done:
        async with tenant_session(engine, org) as s:
            await telegram.record_sent(s, org, done)
    return len(done) + sum(edited)


async def notifications(ctx: dict[str, Any], organization_id: str, event_id: str) -> int:
    """The arq consumer the relay enqueues as `notifications` (registry.py)."""
    if ctx.get("notifier") is None:
        return 0  # no bot configured: nothing to deliver
    try:
        return await notify(
            ctx["work_engine"], uuid.UUID(organization_id), uuid.UUID(event_id), ctx["notifier"]
        )
    except Exception as e:
        # A database failure before the marker rows committed: run again (arq max_tries bounds it).
        log.error("telegram notification failed: %s", type(e).__name__)
        raise Retry(defer=10 * ctx.get("job_try", 1)) from e

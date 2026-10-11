import hashlib
import secrets
import uuid
from typing import Any

import anyio
from fastapi import APIRouter, WebSocket, status
from redis.asyncio import Redis
from redis.exceptions import RedisError
from sqlalchemy.ext.asyncio import AsyncEngine

from app.adapters.pubsub_redis import subscription
from app.api.deps import ClockDep, EngineDep, RedisDep
from app.api.v1.events import EventOut, FeedOut
from app.authz.matrix import Role
from app.config import settings
from app.domain.errors import InvalidInput, RateLimited, RateLimiterUnavailable
from app.realtime.feed import parse_cursor, read_after
from app.realtime.longpoll import project_rows, waiter_slot
from app.services.web_sessions import role_in

router = APIRouter(tags=["realtime"])
TICKET_TTL = 30  # seconds (spec 5.3)
BATCH = 100  # events per frame: the server never reads further ahead of the client than this
FALLBACK = 5.0  # seconds between database reads when no wake-up arrives (pub/sub is a hint only)
SEND_TIMEOUT = 5.0  # a client that cannot take one frame in this time is dropped
# Then the client reconnects with a fresh ticket: access is re-proven at least this often.
LIFETIME = 300.0
SLOT_TTL = LIFETIME + SEND_TIMEOUT + 15  # the shared waiter cap's slot outlives its socket


def _ticket_key(ticket: str) -> str:
    # Stored by hash: a Redis dump never holds a usable ticket.
    return "rt:ticket:" + hashlib.sha256(ticket.encode()).hexdigest()


async def issue_ticket(redis: Redis, user: uuid.UUID, org: uuid.UUID) -> str:
    ticket = secrets.token_urlsafe(32)
    await redis.set(_ticket_key(ticket), f"{user}:{org}", ex=TICKET_TTL)
    return ticket


async def redeem(redis: Redis, ticket: str) -> tuple[uuid.UUID, uuid.UUID] | None:
    """(user, organization) the ticket was minted for, once: GETDEL is atomic across instances."""
    try:
        raw = await redis.getdel(_ticket_key(ticket))
    except (RedisError, OSError):
        return None
    if raw is None:
        return None
    user, org = (raw.decode() if isinstance(raw, bytes) else raw).split(":")
    return uuid.UUID(user), uuid.UUID(org)


def frame(org: uuid.UUID, rows: list[dict[str, Any]], role: Role) -> str:
    """One batch shaped exactly as GET /events answers; `after` moves past events this role may not see."""
    events = [EventOut(**e) for e in project_rows(rows, role)]
    return FeedOut(organization_id=org, events=events, after=rows[-1]["seq"]).model_dump_json()


@router.websocket("/ws")
async def stream(
    ws: WebSocket,
    engine: EngineDep,
    redis: RedisDep,
    clock: ClockDep,
    ticket: str = "",
    after: str = "0",
) -> None:
    """Replay the ticket's organization from `after`, then stream. Every refusal closes before the upgrade."""
    # Exact match, missing refused (as require_same_origin); checked before the ticket is spent.
    if ws.headers.get("origin") != settings.web_origin:
        return await ws.close(status.WS_1008_POLICY_VIOLATION)
    try:
        cursor = parse_cursor(after)
    except InvalidInput:
        return await ws.close(status.WS_1008_POLICY_VIOLATION)
    owner = await redeem(redis, ticket) if ticket else None
    if owner is None:
        return await ws.close(status.WS_1008_POLICY_VIOLATION)
    user, org = owner
    if await role_in(engine, user, org) is None:  # left since the ticket was minted
        return await ws.close(status.WS_1008_POLICY_VIOLATION)
    code: int | None = None
    try:
        async with waiter_slot(redis, org, user, clock.now().timestamp(), SLOT_TTL):
            await ws.accept()
            async with anyio.create_task_group() as tg:
                tg.start_soon(_until_gone, ws, tg.cancel_scope)
                with anyio.move_on_after(LIFETIME) as life:
                    code = await _pump(ws, engine, redis, org, user, cursor)
                if life.cancelled_caught:
                    code = status.WS_1000_NORMAL_CLOSURE  # reconnect with a fresh ticket
                tg.cancel_scope.cancel()
    except (RateLimited, RateLimiterUnavailable):
        return await ws.close(status.WS_1013_TRY_AGAIN_LATER)
    if code is not None:
        with anyio.move_on_after(SEND_TIMEOUT):
            await ws.close(code)


async def _until_gone(ws: WebSocket, scope: anyio.CancelScope) -> None:
    """Client frames are ignored; a disconnect ends the stream at once (slot and subscription freed)."""
    while (await ws.receive())["type"] != "websocket.disconnect":
        pass
    scope.cancel()


async def _pump(
    ws: WebSocket, engine: AsyncEngine, redis: Redis, org: uuid.UUID, user: uuid.UUID, cursor: int
) -> int | None:
    """Pages from the cursor, then one read per wake-up (or FALLBACK). Returns the close code; None drops
    a client that stopped reading (closing it would stall too).

    The next page is read only after the last one was sent, so a slow client holds one page at most."""
    # Subscribed before the first read: an event committed between a read and the wait still wakes it.
    async with subscription(redis, org) as wake:
        while True:
            rows = await read_after(engine, org, cursor, BATCH)
            if not rows:
                await wake(FALLBACK)
                continue
            role = await role_in(engine, user, org)  # re-read before every frame
            if role is None:
                return status.WS_1008_POLICY_VIOLATION
            with anyio.move_on_after(SEND_TIMEOUT) as sent:
                await ws.send_text(frame(org, rows, Role[role.upper()]))
            if sent.cancelled_caught:
                return None
            cursor = rows[-1]["seq"]

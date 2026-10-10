import hashlib
import secrets
import uuid
from typing import Any

from fastapi import APIRouter, WebSocket, status
from redis.asyncio import Redis
from redis.exceptions import RedisError

from app.api.deps import ClockDep, EngineDep, RedisDep
from app.authz.matrix import Role
from app.config import settings
from app.domain.errors import InvalidInput, RateLimited, RateLimiterUnavailable
from app.realtime.feed import parse_cursor, read_after
from app.realtime.longpoll import waiter_slot
from app.realtime.projection import project
from app.services.web_sessions import role_in

router = APIRouter(tags=["realtime"])
TICKET_TTL = 30  # seconds (spec 5.3)
BATCH = 100  # events per frame: the server never reads further ahead of the client than this
FALLBACK = 5.0  # seconds between database reads when no wake-up arrives (pub/sub is a hint only)
SEND_TIMEOUT = 5.0  # a client that cannot take one frame in this time is dropped
LIFETIME = (
    300.0  # then the client reconnects with a fresh ticket: access is re-proven at least this often
)
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


def frame(org: uuid.UUID, rows: list[dict[str, Any]], role: Role) -> dict[str, Any]:
    """One batch as GET /events shapes it; `after` moves past events this role may not see."""
    events = []
    for r in rows:
        data = project(r["type"], r["payload"], role)
        if data is not None:
            events.append(
                {
                    "id": str(r["id"]),
                    "seq": r["seq"],
                    "type": r["type"],
                    "data": data,
                    "created_at": r["created_at"].isoformat(),
                }
            )
    return {"organization_id": str(org), "events": events, "after": rows[-1]["seq"]}


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
    try:
        async with waiter_slot(redis, org, user, clock.now().timestamp()):
            await ws.accept()
            while rows := await read_after(engine, org, cursor, BATCH):
                role = await role_in(engine, user, org)
                if role is None:
                    return await ws.close(status.WS_1008_POLICY_VIOLATION)
                await ws.send_json(frame(org, rows, Role[role.upper()]))
                cursor = rows[-1]["seq"]
            while (await ws.receive())["type"] != "websocket.disconnect":
                pass
    except (RateLimited, RateLimiterUnavailable):
        return await ws.close(status.WS_1013_TRY_AGAIN_LATER)

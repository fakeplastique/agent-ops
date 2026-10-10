import uuid
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Header, Query, Request
from pydantic import BaseModel

from app.api.deps import ActorDep, ClockDep, EngineDep, RedisDep, session_user
from app.authz.matrix import Role
from app.authz.principals import UserPrincipal
from app.authz.resolver import resolve_bearer
from app.domain.errors import Forbidden, InvalidInput, NotFoundError, Unauthenticated
from app.realtime.longpoll import MAX_WAIT, poll, waiter_slot

router = APIRouter(prefix="/events", tags=["realtime"])


class EventOut(BaseModel):
    id: uuid.UUID
    seq: int
    type: str
    data: dict[str, Any]
    created_at: datetime


class FeedOut(BaseModel):
    # seq is per organization: a client resets its cursor when this changes
    organization_id: uuid.UUID
    events: list[EventOut]
    after: int  # the cursor for the next poll (past dropped events too)


_MAX_SEQ = 2**63 - 1  # bigint


def _cursor(raw: str) -> int:
    """Plain ASCII digits only: int() alone would also take "1_0", "+3" and " 3"."""
    if not (raw.isascii() and raw.isdigit()) or len(raw) > 19 or int(raw) > _MAX_SEQ:
        raise InvalidInput("after must be a sequence number (digits only)")
    return int(raw)


@router.get("", response_model=FeedOut)
async def long_poll(
    request: Request,
    actor: ActorDep,
    engine: EngineDep,
    redis: RedisDep,
    clock: ClockDep,
    after: Annotated[str, Query(description="Last seq seen (digits only)")] = "0",
    wait: Annotated[
        int, Query(ge=0, le=MAX_WAIT, description="Seconds to wait for a first event")
    ] = MAX_WAIT,
    limit: Annotated[int, Query(ge=1, le=100)] = 100,
    authorization: Annotated[str | None, Header()] = None,
) -> FeedOut:
    """The selected organization's events after `after`, in seq order, projected to the caller's role.
    At-least-once: clients de-duplicate by seq. The organization comes from the credential only."""
    if not isinstance(actor, UserPrincipal):
        raise Forbidden("the event feed is for users")
    org = actor.organization_id

    async def recheck() -> Role:
        # The resolvers behind ActorDep, minus the login-failure counter: a lapse is no failed login.
        # A lapse (or a switch to another org) is this org's 404.
        try:
            if authorization is not None:
                now = (await resolve_bearer(engine, authorization)).principal
            else:
                now = await session_user(request, engine, clock)
        except (Unauthenticated, Forbidden):
            raise NotFoundError from None
        if not isinstance(now, UserPrincipal) or now.user_id != actor.user_id:
            raise NotFoundError
        if now.organization_id != org:
            raise NotFoundError
        return now.role

    cursor = _cursor(after)
    async with waiter_slot(redis, org, actor.user_id, clock.now().timestamp()):
        events, cursor = await poll(
            engine, org, cursor, wait, limit, recheck, request.is_disconnected
        )
    return FeedOut(organization_id=org, events=[EventOut(**e) for e in events], after=cursor)

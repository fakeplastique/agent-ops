import uuid
from datetime import datetime
from functools import partial
from typing import Annotated, Any

from fastapi import APIRouter, Header, Query, Request
from pydantic import BaseModel

from app.api.deps import COOKIE, ActorDep, ClockDep, EngineDep, RedisDep
from app.authz.principals import UserPrincipal
from app.domain.errors import Forbidden
from app.realtime import access
from app.realtime.access import credential_of
from app.realtime.feed import parse_cursor
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

    # The credential ActorDep resolved, re-verified before a batch leaves (shared with /ws).
    cred = credential_of(authorization, request.cookies.get(COOKIE))
    recheck = partial(access.recheck, engine, cred, actor.user_id, org, clock)

    cursor = parse_cursor(after)
    async with waiter_slot(redis, org, actor.user_id, clock.now().timestamp()):
        events, cursor = await poll(
            engine, org, cursor, wait, limit, recheck, request.is_disconnected
        )
    return FeedOut(organization_id=org, events=[EventOut(**e) for e in events], after=cursor)

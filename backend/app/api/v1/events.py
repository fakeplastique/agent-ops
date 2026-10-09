import uuid
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Header, Query, Request
from pydantic import BaseModel

from app.api.deps import ActorDep, ClockDep, EngineDep, RedisDep, get_actor
from app.authz.matrix import Role
from app.authz.principals import UserPrincipal
from app.domain.errors import Forbidden, NotFoundError, Unauthenticated
from app.realtime.longpoll import MAX_WAIT, poll

router = APIRouter(prefix="/events", tags=["realtime"])


class EventOut(BaseModel):
    id: uuid.UUID
    seq: int
    type: str
    data: dict[str, Any]
    created_at: datetime


class FeedOut(BaseModel):
    organization_id: (
        uuid.UUID
    )  # seq is per organization: a client resets its cursor when this changes
    events: list[EventOut]
    after: int  # the cursor for the next poll (past dropped events too)


@router.get("", response_model=FeedOut)
async def long_poll(
    request: Request,
    actor: ActorDep,
    engine: EngineDep,
    redis: RedisDep,
    clock: ClockDep,
    after: Annotated[int, Query(ge=0, le=2**63 - 1)] = 0,
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
        # Same resolver as the request itself; a lapse (or a switch to another org) is this org's 404.
        try:
            now = await get_actor(request, engine, redis, clock, authorization)
        except (Unauthenticated, Forbidden):
            raise NotFoundError from None
        if not isinstance(now, UserPrincipal) or (now.user_id, now.organization_id) != (
            actor.user_id,
            org,
        ):
            raise NotFoundError
        return now.role

    events, cursor = await poll(engine, org, after, wait, limit, recheck)
    return FeedOut(organization_id=org, events=[EventOut(**e) for e in events], after=cursor)

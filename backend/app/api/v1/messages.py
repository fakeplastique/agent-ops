import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response

from app.api.deps import ActorDep, ClockDep, EngineDep, write_limit
from app.api.pagination import Page, PaginationParams, paginate
from app.api.schemas.decisions import MessageIn, MessageOut, MessagePosted
from app.api.v1.decisions import PATH, agent_session, user_of
from app.db.session import tenant_session
from app.services import decision_reads, messages

router = APIRouter(prefix="/decision-requests/{decision_request_id}/messages", tags=["messages"])


@router.post(
    "", status_code=201, response_model=MessagePosted, dependencies=[Depends(write_limit)]
)
async def post(
    decision_request_id: uuid.UUID,
    body: MessageIn,
    response: Response,
    actor: ActorDep,
    engine: EngineDep,
    clock: ClockDep,
) -> MessagePosted:
    """An agent or member posts to an open discussion (pending_discussion through pending_approval)."""
    async with tenant_session(engine, actor.organization_id) as s:
        out = await messages.post_message(
            s, actor.organization_id, clock, decision_request_id=decision_request_id,
            body=body.body, agent_session_id=await agent_session(s, actor, clock),
            user_id=user_of(actor),
        )  # fmt: skip
    response.headers["Location"] = f"{PATH}/{decision_request_id}/messages"
    return MessagePosted(**out)


@router.get("", response_model=Page[MessageOut])
async def list_messages(
    decision_request_id: uuid.UUID,
    request: Request,
    actor: ActorDep,
    engine: EngineDep,
    page: Annotated[PaginationParams, Depends()],
) -> Page[MessageOut]:
    async with tenant_session(engine, actor.organization_id) as s:
        rows, total = await decision_reads.list_messages(
            s, actor.organization_id, decision_request_id, page.skip, page.limit
        )
    return paginate(request, [MessageOut(**m) for m in rows], total, page.skip, page.limit)

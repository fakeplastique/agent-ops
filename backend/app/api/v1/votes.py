import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response

from app.api.deps import ActorDep, ClockDep, EngineDep, write_limit
from app.api.pagination import PaginationParams, paginate
from app.api.schemas.decisions import VoteCast, VoteIn, VoteOut, VotesPage
from app.api.v1.decisions import PATH, require_agent, session_of, viewer_session
from app.config import settings
from app.db.session import tenant_session
from app.domain.receipt import KeyRing
from app.services import decisions, votes

router = APIRouter(prefix="/decision-requests/{decision_request_id}/votes", tags=["votes"])


@router.post("", status_code=201, response_model=VoteCast, dependencies=[Depends(write_limit)])
async def cast(
    decision_request_id: uuid.UUID,
    body: VoteIn,
    response: Response,
    actor: ActorDep,
    engine: EngineDep,
    clock: ClockDep,
) -> VoteCast:
    """A reviewing agent session votes on the request's current version and round."""
    agent = require_agent(actor)
    async with tenant_session(engine, agent.organization_id) as s:
        out = await decisions.cast_vote(
            s, agent.organization_id, clock, keys=KeyRing.parse(settings.receipt_keys),
            decision_request_id=decision_request_id,
            agent_session_id=await session_of(s, agent, clock), **body.model_dump(),
        )  # fmt: skip
    response.headers["Location"] = f"{PATH}/{decision_request_id}/votes"
    return VoteCast(**out)


@router.get("", response_model=VotesPage)
async def list_votes(
    decision_request_id: uuid.UUID,
    request: Request,
    actor: ActorDep,
    engine: EngineDep,
    page: Annotated[PaginationParams, Depends()],
) -> VotesPage:
    """Round 1 stays blind until it closes: before that a viewer sees only its own vote and the count."""
    async with tenant_session(engine, actor.organization_id) as s:
        seen = await votes.visible_votes(
            s, actor.organization_id, decision_request_id,
            viewer_session_id=await viewer_session(s, actor),
        )  # fmt: skip
    rows = [VoteOut(**v) for v in seen["votes"]]
    base = paginate(
        request, rows[page.skip : page.skip + page.limit], len(rows), page.skip, page.limit
    )
    return VotesPage(**base.model_dump(), cast=seen["cast"], quorum=seen["quorum"])

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, Query, Request
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.api import idempotency
from app.api.deps import ActorDep, ClockDep, EngineDep, write_limit
from app.api.pagination import Page, PaginationParams, paginate
from app.api.schemas.decisions import DecisionIn, DecisionOut, ReviseIn, RiskIn, Sort
from app.authz.principals import AgentPrincipal, Principal
from app.db.session import tenant_session
from app.domain.errors import Forbidden
from app.domain.statuses import Status
from app.ports.clock import Clock
from app.services import decision_reads, decisions
from app.services.sessions import get_or_create_session

PATH = "/api/v1/decision-requests"
REST_LABEL = "rest"  # REST has no session id: one REST session per agent identity (D12)

router = APIRouter(prefix="/decision-requests", tags=["decision-requests"])


async def agent_session(s: AsyncSession, actor: Principal, clock: Clock) -> uuid.UUID | None:
    if not isinstance(actor, AgentPrincipal):
        return None
    sid, _ = await get_or_create_session(
        s, actor.organization_id, actor.agent_id, clock, argument_label=REST_LABEL
    )
    return sid


async def viewer_session(s: AsyncSession, actor: Principal) -> uuid.UUID | None:
    if not isinstance(actor, AgentPrincipal):
        return None
    return await decision_reads.find_session(
        s, actor.organization_id, actor.agent_id, REST_LABEL
    )


def user_of(actor: Principal) -> uuid.UUID | None:
    return None if isinstance(actor, AgentPrincipal) else actor.user_id


def require_agent(actor: Principal) -> AgentPrincipal:
    if not isinstance(actor, AgentPrincipal):
        raise Forbidden("only an agent token may do this")
    return actor


async def read(s: AsyncSession, actor: Principal, r: uuid.UUID) -> DecisionOut:
    return DecisionOut(**await decision_reads.get_decision(s, actor.organization_id, r))


def _created(body: Any, status: int = 201) -> JSONResponse:
    headers = {"Location": f"{PATH}/{body['id']}"} if status == 201 else None
    return JSONResponse(body, status_code=status, headers=headers)


@router.post(
    "", status_code=201, response_model=DecisionOut, dependencies=[Depends(write_limit)]
)
async def create(
    body: DecisionIn,
    actor: ActorDep,
    engine: EngineDep,
    clock: ClockDep,
    idempotency_key: Annotated[str | None, Header()] = None,
) -> JSONResponse:
    """Create a request; a repeated Idempotency-Key with the same body returns the first answer."""
    o, data = actor.organization_id, body.model_dump()
    who = actor.agent_id if isinstance(actor, AgentPrincipal) else actor.user_id
    route = f"POST {PATH}"
    async with tenant_session(engine, o) as s:
        if idempotency_key is not None:
            replay = await idempotency.begin(s, o, who, idempotency_key, route, data, clock)
            if replay:
                return _created(replay.body, replay.status)
        made = await decisions.create_decision(
            s, o, clock, **data,
            agent_session_id=await agent_session(s, actor, clock), user_id=user_of(actor),
        )  # fmt: skip
        out = (await read(s, actor, made["id"])).model_dump(mode="json")
        if idempotency_key is not None:
            await idempotency.finish(s, o, who, idempotency_key, route, 201, out)
    return _created(out)


@router.get("", response_model=Page[DecisionOut])
async def list_decisions(
    request: Request,
    actor: ActorDep,
    engine: EngineDep,
    page: Annotated[PaginationParams, Depends()],
    status: Status | None = None,
    risk_level: RiskIn | None = None,
    agent_id: uuid.UUID | None = None,
    q: Annotated[str | None, Query(min_length=1, max_length=200)] = None,
    sort: Sort = "-created_at",
) -> Page[DecisionOut]:
    async with tenant_session(engine, actor.organization_id) as s:
        rows, total = await decision_reads.list_decisions(
            s, actor.organization_id, status=status.value if status else None,
            risk_level=risk_level, agent_id=agent_id, q=q, sort=sort,
            skip=page.skip, limit=page.limit,
        )  # fmt: skip
    items = [DecisionOut(**r) for r in rows]
    return paginate(request, items, total, page.skip, page.limit)


@router.get("/{decision_request_id}", response_model=DecisionOut)
async def get_decision(
    decision_request_id: uuid.UUID, actor: ActorDep, engine: EngineDep
) -> DecisionOut:
    async with tenant_session(engine, actor.organization_id) as s:
        return await read(s, actor, decision_request_id)


@router.post(
    "/{decision_request_id}/revise",
    response_model=DecisionOut,
    dependencies=[Depends(write_limit)],
)
async def revise(
    decision_request_id: uuid.UUID,
    body: ReviseIn,
    actor: ActorDep,
    engine: EngineDep,
    clock: ClockDep,
) -> DecisionOut:
    """The requesting agent identity changes a pending request (version + 1)."""
    agent = require_agent(actor)
    async with tenant_session(engine, agent.organization_id) as s:
        await decisions.revise_decision(
            s, agent.organization_id, clock, decision_request_id=decision_request_id,
            agent_session_id=await agent_session(s, agent, clock), **body.model_dump(),
        )  # fmt: skip
        return await read(s, agent, decision_request_id)


@router.post(
    "/{decision_request_id}/withdraw",
    response_model=DecisionOut,
    dependencies=[Depends(write_limit)],
)
async def withdraw(
    decision_request_id: uuid.UUID, actor: ActorDep, engine: EngineDep, clock: ClockDep
) -> DecisionOut:
    """The requesting agent identity or an org_admin cancels an undecided request."""
    async with tenant_session(engine, actor.organization_id) as s:
        await decisions.withdraw_decision(
            s, actor.organization_id, clock, decision_request_id=decision_request_id,
            agent_session_id=await agent_session(s, actor, clock), user_id=user_of(actor),
        )  # fmt: skip
        return await read(s, actor, decision_request_id)

import uuid
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, Field

from app.api.deps import ActorDep, AdminDep, EngineDep, write_limit
from app.api.pagination import Page, PaginationParams, paginate
from app.api.schemas.base import BaseSchema
from app.db.session import tenant_session
from app.services import agents, tokens

router = APIRouter(prefix="/agents", tags=["agents"])


class AgentIn(BaseSchema):
    name: str = Field(max_length=80)
    description: str = Field("", max_length=1000)
    vendor: str | None = Field(None, max_length=80)
    owner_user_id: uuid.UUID | None = None  # defaults to the admin creating it


class AgentOut(BaseModel):
    id: uuid.UUID
    name: str
    description: str
    vendor: str | None
    owner_user_id: uuid.UUID
    status: str
    created_at: datetime


class AgentToken(BaseModel):
    id: uuid.UUID
    token: str  # shown once: only this reply ever carries it


class AgentCreated(AgentOut):
    token: str  # shown once


@router.post(
    "", status_code=201, response_model=AgentCreated, dependencies=[Depends(write_limit)]
)
async def create(
    body: AgentIn, response: Response, admin: AdminDep, engine: EngineDep
) -> AgentCreated:
    async with tenant_session(engine, admin.organization_id) as s:
        row, raw = await agents.create_agent(
            s, admin.organization_id, admin.user_id, body.name, body.description, body.vendor,
            body.owner_user_id or admin.user_id,
        )  # fmt: skip
    response.headers["Location"] = f"/api/v1/agents/{row['id']}"
    return AgentCreated(**row, token=raw)


@router.get("", response_model=Page[AgentOut])
async def list_agents(
    request: Request, actor: ActorDep, engine: EngineDep,
    page: Annotated[PaginationParams, Depends()],
) -> Page[AgentOut]:  # fmt: skip
    async with tenant_session(engine, actor.organization_id) as s:
        rows, total = await agents.list_agents(s, actor.organization_id, page.skip, page.limit)
    return paginate(request, [AgentOut(**r) for r in rows], total, page.skip, page.limit)


@router.get("/{agent_id}", response_model=AgentOut)
async def get_agent(agent_id: uuid.UUID, actor: ActorDep, engine: EngineDep) -> AgentOut:
    async with tenant_session(engine, actor.organization_id) as s:
        return AgentOut(**await agents.get_agent(s, actor.organization_id, agent_id))


@router.post("/{agent_id}/rotate", response_model=AgentToken, dependencies=[Depends(write_limit)])
async def rotate(agent_id: uuid.UUID, admin: AdminDep, engine: EngineDep) -> AgentToken:
    """A new token; the old one fails from the next request on (same transaction replaces the hash)."""
    async with tenant_session(engine, admin.organization_id) as s:
        raw = await tokens.issue_agent_token(s, admin.organization_id, agent_id, admin.user_id)
    return AgentToken(id=agent_id, token=raw)


@router.delete("/{agent_id}", status_code=204, dependencies=[Depends(write_limit)])
async def revoke(agent_id: uuid.UUID, admin: AdminDep, engine: EngineDep) -> Response:
    async with tenant_session(engine, admin.organization_id) as s:
        await tokens.revoke_agent(s, admin.organization_id, agent_id, admin.user_id)
    return Response(status_code=204)

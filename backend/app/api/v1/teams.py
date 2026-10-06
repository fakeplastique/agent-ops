import uuid
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, Field

from app.api.deps import ActorDep, AdminDep, EngineDep, write_limit
from app.api.pagination import Page, PaginationParams, paginate
from app.api.schemas.base import BaseSchema
from app.db.session import tenant_session
from app.services import teams

router = APIRouter(prefix="/teams", tags=["teams"])


class TeamIn(BaseSchema):
    name: str = Field(max_length=80)
    parent_team_id: uuid.UUID | None = None


class TeamPatch(BaseSchema):
    name: str | None = Field(None, max_length=80)
    parent_team_id: uuid.UUID | None = None  # explicit null moves the team to the top


class TeamOut(BaseModel):
    id: uuid.UUID
    name: str
    parent_team_id: uuid.UUID | None
    created_at: datetime


@router.post("", status_code=201, response_model=TeamOut, dependencies=[Depends(write_limit)])
async def create(body: TeamIn, response: Response, admin: AdminDep, engine: EngineDep) -> TeamOut:
    async with tenant_session(engine, admin.organization_id) as s:
        row = await teams.create_team(
            s, admin.organization_id, admin.user_id, body.name, body.parent_team_id
        )
    response.headers["Location"] = f"/api/v1/teams/{row['id']}"
    return TeamOut(**row)


@router.get("", response_model=Page[TeamOut])
async def list_teams(
    request: Request, actor: ActorDep, engine: EngineDep,
    page: Annotated[PaginationParams, Depends()],
) -> Page[TeamOut]:  # fmt: skip
    async with tenant_session(engine, actor.organization_id) as s:
        rows, total = await teams.list_teams(s, actor.organization_id, page.skip, page.limit)
    return paginate(request, [TeamOut(**r) for r in rows], total, page.skip, page.limit)


@router.get("/{team_id}", response_model=TeamOut)
async def get_team(team_id: uuid.UUID, actor: ActorDep, engine: EngineDep) -> TeamOut:
    async with tenant_session(engine, actor.organization_id) as s:
        return TeamOut(**await teams.get_team(s, actor.organization_id, team_id))


@router.patch("/{team_id}", response_model=TeamOut, dependencies=[Depends(write_limit)])
async def update(
    team_id: uuid.UUID, body: TeamPatch, admin: AdminDep, engine: EngineDep
) -> TeamOut:
    # Only the fields sent: an absent parent stays, an explicit null detaches.
    fields = body.model_dump(exclude_unset=True)
    async with tenant_session(engine, admin.organization_id) as s:
        return TeamOut(
            **await teams.update_team(s, admin.organization_id, admin.user_id, team_id, fields)
        )


@router.delete("/{team_id}", status_code=204, dependencies=[Depends(write_limit)])
async def delete(team_id: uuid.UUID, admin: AdminDep, engine: EngineDep) -> Response:
    async with tenant_session(engine, admin.organization_id) as s:
        await teams.delete_team(s, admin.organization_id, admin.user_id, team_id)
    return Response(status_code=204)

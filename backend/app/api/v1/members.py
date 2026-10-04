import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, Field

from app.api.deps import ActorDep, AdminDep, EngineDep, write_limit
from app.api.pagination import Page, PaginationParams, paginate
from app.api.schemas.base import BaseSchema
from app.db.session import tenant_session
from app.services import members

router = APIRouter(prefix="/members", tags=["members"])


class MemberOut(BaseModel):
    user_id: uuid.UUID
    email: str
    full_name: str
    role: str
    team_id: uuid.UUID | None = None


class RoleIn(BaseSchema):
    role: str = Field(max_length=30)


@router.get("", response_model=Page[MemberOut])
async def list_members(
    request: Request,
    actor: ActorDep,
    engine: EngineDep,
    page: Annotated[PaginationParams, Depends()],
) -> Page[MemberOut]:
    async with tenant_session(engine, actor.organization_id) as s:
        rows, total = await members.list_members(s, actor.organization_id, page.skip, page.limit)
    return paginate(request, [MemberOut(**r) for r in rows], total, page.skip, page.limit)


@router.patch("/{user_id}", response_model=MemberOut, dependencies=[Depends(write_limit)])
async def change_role(
    user_id: uuid.UUID, body: RoleIn, admin: AdminDep, engine: EngineDep
) -> MemberOut:
    async with tenant_session(engine, admin.organization_id) as s:
        await members.set_role(s, admin.organization_id, user_id, body.role, admin.user_id)
        return MemberOut(**await members.get_member(s, admin.organization_id, user_id))


@router.delete("/{user_id}", status_code=204, dependencies=[Depends(write_limit)])
async def remove(user_id: uuid.UUID, admin: AdminDep, engine: EngineDep) -> Response:
    async with tenant_session(engine, admin.organization_id) as s:
        await members.remove_member(s, admin.organization_id, user_id, admin.user_id)
    return Response(status_code=204)

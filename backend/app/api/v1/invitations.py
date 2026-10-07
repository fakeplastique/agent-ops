import uuid
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, Field

from app.api.deps import (
    COOKIE,
    AdminDep,
    ClockDep,
    EngineDep,
    SessionDep,
    user_write_limit,
    write_limit,
)
from app.api.errors import error_responses
from app.api.pagination import Page, PaginationParams, paginate
from app.api.schemas.base import BaseSchema
from app.authz.origin import require_same_origin
from app.db.session import tenant_session
from app.services import invitations, web_sessions

router = APIRouter(prefix="/invitations", tags=["invitations"])


class InvitationIn(BaseSchema):
    email: str = Field(max_length=300)
    role: str = Field(max_length=30)
    team_id: uuid.UUID | None = None


class InvitationOut(BaseModel):
    id: uuid.UUID
    email: str
    role: str
    team_id: uuid.UUID | None = None
    expires_at: datetime


class InvitationCreated(InvitationOut):
    token: str  # shown once


class AcceptIn(BaseSchema):
    token: str = Field(max_length=200)


@router.post(
    "", status_code=201, response_model=InvitationCreated, dependencies=[Depends(write_limit)]
)
async def create(
    body: InvitationIn, response: Response, admin: AdminDep, engine: EngineDep, clock: ClockDep
) -> InvitationCreated:
    async with tenant_session(engine, admin.organization_id) as s:
        inv, raw = await invitations.create_invitation(
            s, admin.organization_id, admin.user_id, body.email, body.role, body.team_id, clock
        )
    response.headers["Location"] = f"/api/v1/invitations/{inv}"
    return InvitationCreated(
        id=inv, email=body.email.strip().lower(), role=body.role, team_id=body.team_id,
        expires_at=clock.now() + invitations.TTL, token=raw,
    )  # fmt: skip


@router.get("", response_model=Page[InvitationOut])
async def list_pending(
    request: Request, admin: AdminDep, engine: EngineDep, clock: ClockDep,
    page: Annotated[PaginationParams, Depends()],
) -> Page[InvitationOut]:  # fmt: skip
    async with tenant_session(engine, admin.organization_id) as s:
        rows = await invitations.pending_invitations(s, admin.organization_id, clock)
    out = [InvitationOut(**r) for r in rows]
    return paginate(
        request, out[page.skip : page.skip + page.limit], len(out), page.skip, page.limit
    )


@router.delete("/{invitation_id}", status_code=204, dependencies=[Depends(write_limit)])
async def revoke(
    invitation_id: uuid.UUID, admin: AdminDep, engine: EngineDep, clock: ClockDep
) -> Response:
    async with tenant_session(engine, admin.organization_id) as s:
        await invitations.revoke_invitation(
            s, admin.organization_id, invitation_id, clock, admin.user_id
        )
    return Response(status_code=204)


class Joined(BaseModel):
    organization_id: uuid.UUID
    role: str


@router.post(
    "/accept",
    response_model=Joined,
    dependencies=[Depends(require_same_origin), Depends(user_write_limit)],
    responses=error_responses(404, 409),
)
async def accept(
    body: AcceptIn, request: Request, session: SessionDep, engine: EngineDep, clock: ClockDep
) -> Joined:
    """The signed-in user joins the invited organization and switches to it."""
    org, role = await invitations.accept_invitation(engine, session.user_id, body.token, clock)
    await web_sessions.select_organization(engine, request.cookies[COOKIE], session.user_id, org)
    return Joined(organization_id=org, role=role)

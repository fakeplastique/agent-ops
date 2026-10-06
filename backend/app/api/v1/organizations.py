import uuid

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import text

from app.api.deps import COOKIE, ActorDep, ClockDep, EngineDep, SessionDep, user_write_limit
from app.api.errors import error_responses
from app.api.schemas.base import BaseSchema
from app.authz.origin import require_same_origin
from app.db.session import tenant_session
from app.domain.errors import NotFoundError
from app.services import members, web_sessions

router = APIRouter(prefix="/organizations", tags=["organizations"])


class OrganizationIn(BaseSchema):
    name: str = Field(max_length=200)
    slug: str = Field(max_length=100)


class OrganizationOut(BaseModel):
    id: uuid.UUID
    name: str
    slug: str
    plan_key: str


async def _read(engine: EngineDep, org: uuid.UUID) -> OrganizationOut:
    async with tenant_session(engine, org) as s:
        row = (await s.execute(text("select id, name, slug, plan_key from organizations"))).first()
    if row is None:
        raise NotFoundError
    return OrganizationOut(id=row.id, name=row.name, slug=row.slug, plan_key=row.plan_key)


@router.post(
    "",
    status_code=201,
    response_model=OrganizationOut,
    dependencies=[Depends(require_same_origin), Depends(user_write_limit)],
    responses=error_responses(409),
)
async def create_organization(
    body: OrganizationIn,
    request: Request,
    response: Response,
    session: SessionDep,
    engine: EngineDep,
    clock: ClockDep,
) -> OrganizationOut:
    """A signed-in user founds an organization, becomes its org_admin and switches to it."""
    org = await members.create_organization(engine, session.user_id, body.name, body.slug)
    await web_sessions.select_organization(engine, request.cookies[COOKIE], session.user_id, org)
    response.headers["Location"] = f"/api/v1/organizations/{org}"
    return await _read(engine, org)


@router.get("/{organization_id}", response_model=OrganizationOut)
async def get_organization(
    organization_id: uuid.UUID, actor: ActorDep, engine: EngineDep
) -> OrganizationOut:
    if organization_id != actor.organization_id:  # another tenant's id looks like a missing one
        raise NotFoundError
    return await _read(engine, organization_id)

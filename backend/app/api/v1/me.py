from typing import Annotated, Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.api.deps import EngineDep, SessionDep, get_current_principal
from app.authz.principals import Principal, UserPrincipal
from app.services import web_sessions

router = APIRouter(prefix="/me", tags=["me"])


class MeOut(BaseModel):
    type: Literal["user", "agent"]
    id: str
    organization_id: str
    role: str | None = None


@router.get("", response_model=MeOut)
def get_me(principal: Annotated[Principal, Depends(get_current_principal)]) -> MeOut:
    if isinstance(principal, UserPrincipal):
        return MeOut(
            type="user",
            id=str(principal.user_id),
            organization_id=str(principal.organization_id),
            role=principal.role.name.lower(),
        )
    return MeOut(
        type="agent", id=str(principal.agent_id), organization_id=str(principal.organization_id)
    )


class MyOrganization(BaseModel):
    id: str
    name: str
    role: str


class MyOrganizations(BaseModel):
    items: list[MyOrganization]


@router.get("/organizations", response_model=MyOrganizations)
async def my_organizations(session: SessionDep, engine: EngineDep) -> MyOrganizations:
    """The signed-in user's own memberships (any tenant they belong to); the web session only."""
    rows = await web_sessions.organizations_of(engine, session.user_id)
    return MyOrganizations(items=[MyOrganization(**r) for r in rows])

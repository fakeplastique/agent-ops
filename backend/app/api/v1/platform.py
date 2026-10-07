import uuid
from typing import Annotated

from fastapi import APIRouter, Depends

from app.api.deps import ActorDep, ClockDep, EngineDep, write_limit
from app.api.v1.plan import PlanIn, PlanOut
from app.authz.principals import UserPrincipal
from app.config import settings
from app.db.session import tenant_session
from app.services import plans

router = APIRouter(prefix="/platform", tags=["platform"])


async def platform_admin(actor: ActorDep, engine: EngineDep) -> UserPrincipal:
    """403 before any organization id is looked at: a non-admin learns nothing about other tenants."""
    async with tenant_session(engine, actor.organization_id) as s:
        return await plans.require_platform_admin(s, actor, settings.platform_admin_emails)


@router.put(
    "/organizations/{organization_id}/plan",
    response_model=PlanOut,
    dependencies=[Depends(write_limit)],
)
async def set_plan(
    organization_id: uuid.UUID,
    body: PlanIn,
    admin: Annotated[UserPrincipal, Depends(platform_admin)],
    engine: EngineDep,
    clock: ClockDep,
) -> PlanOut:
    """Platform admins (PLATFORM_ADMIN_EMAILS) set any organization's plan; it applies to the next request."""
    async with tenant_session(engine, organization_id) as s:
        await plans.change_plan(s, organization_id, body.plan, admin.user_id, "platform")
        return PlanOut(**await plans.read_plan(s, organization_id, clock))

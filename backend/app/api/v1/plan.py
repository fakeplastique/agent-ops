from typing import Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.api.deps import ActorDep, ClockDep, EngineDep, write_limit
from app.api.schemas.base import BaseSchema
from app.config import settings
from app.db.session import tenant_session
from app.services import plans

router = APIRouter(prefix="/organizations/current/plan", tags=["plan"])


class PlanIn(BaseSchema):
    plan: Literal["free", "pro", "enterprise"]


class Limits(BaseModel):
    requests_per_month: int | None  # None = unlimited
    max_quorum: int | None  # None = any
    max_rounds: int
    max_levels: int


class Meter(BaseModel):
    used: int
    limit: int | None
    period: str  # UTC month, YYYY-MM


class Usage(BaseModel):
    requests: Meter


class PlanOut(BaseModel):
    plan: str
    limits: Limits
    usage: Usage
    flags: list[str]


@router.get("", response_model=PlanOut)
async def read_plan(actor: ActorDep, engine: EngineDep, clock: ClockDep) -> PlanOut:
    """Any member (or agent) of the organization: its plan, limits, this month's usage and the flags on."""
    async with tenant_session(engine, actor.organization_id) as s:
        return PlanOut(**await plans.read_plan(s, actor.organization_id, clock))


@router.put("", response_model=PlanOut, dependencies=[Depends(write_limit)])
async def switch_plan(body: PlanIn, actor: ActorDep, engine: EngineDep, clock: ClockDep) -> PlanOut:
    """Demo mode only: an org admin switches their own organization's plan (403 otherwise)."""
    async with tenant_session(engine, actor.organization_id) as s:
        await plans.switch_own_plan(s, actor, body.plan, settings.demo_mode)
        return PlanOut(**await plans.read_plan(s, actor.organization_id, clock))

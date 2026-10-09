import uuid
from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from app.api.deps import ActorDep, ClockDep, EngineDep
from app.api.gating import gate
from app.db.session import tenant_session
from app.services import stats

router = APIRouter(prefix="/stats", tags=["stats"])
ANALYTICS_GATE = gate("analytics")
Days = Annotated[int, Query(ge=1, le=366, description="Whole UTC days back, today included")]


class AgentVotes(BaseModel):
    agent_id: uuid.UUID
    name: str
    votes: int


class SummaryOut(BaseModel):
    first_day: date
    last_day: date
    days: int
    requests: int
    by_status: dict[str, int]
    by_risk: dict[str, int]
    auto_approval_rate: float | None  # None when nothing was decided yet
    median_seconds_to_decision: float | None
    average_seconds_to_decision: float | None
    votes_per_agent: list[AgentVotes]


class Point(BaseModel):
    date: date
    requests: int
    approved: int
    rejected: int
    auto_approved: int


class TimeseriesOut(BaseModel):
    first_day: date
    last_day: date
    days: int
    points: list[Point]


@router.get("/summary", response_model=SummaryOut, dependencies=[Depends(ANALYTICS_GATE)])
async def read_summary(
    actor: ActorDep, engine: EngineDep, clock: ClockDep, days: Days = 30
) -> SummaryOut:
    """Any member or agent of a Pro organization: its own requests created in the window."""
    async with tenant_session(engine, actor.organization_id) as s:
        return SummaryOut(**await stats.summary(s, actor.organization_id, clock, days))


@router.get("/timeseries", response_model=TimeseriesOut, dependencies=[Depends(ANALYTICS_GATE)])
async def read_timeseries(
    actor: ActorDep, engine: EngineDep, clock: ClockDep, days: Days = 30
) -> TimeseriesOut:
    """One point per UTC day: requests created, and outcomes by the day they were decided."""
    async with tenant_session(engine, actor.organization_id) as s:
        return TimeseriesOut(**await stats.timeseries(s, actor.organization_id, clock, days))

import uuid
from datetime import datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, Field, StringConstraints

from app.api.deps import ActorDep, AdminDep, EngineDep, write_limit
from app.api.errors import error_responses
from app.api.gating import gate
from app.api.pagination import Page, PaginationParams, paginate
from app.api.schemas.base import BaseSchema
from app.db.session import tenant_session
from app.services import policies

router = APIRouter(prefix="/escalation-policies", tags=["escalation-policies"])
# A per-team policy needs the flag; the org default does not (the service checks again for other callers).
TEAM_GATE = gate(
    policies.TEAM_FLAG,
    when=lambda body: body.get("team_id") is not None,
    probe={"team_id": str(uuid.uuid4()), "levels": [{"role": "manager", "timeout_minutes": 60}]},
)

# strict: a bool or a numeric string never passes as a count (True must not become quorum 1).
Count = Annotated[int, Field(ge=1, le=10_000, strict=True)]
Minutes = Annotated[int, Field(ge=1, le=43_200, strict=True)]  # up to 30 days
ActionType = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_.-]{0,63}$")]
Risk = Literal["low", "medium", "high"]


class LevelIn(BaseSchema):
    role: Literal["team_lead", "manager", "org_admin"]
    timeout_minutes: Minutes


class PolicyFields(BaseSchema):
    name: str = Field("default", max_length=80)
    quorum_size: Count = 3
    require_distinct_agents: bool = Field(True, strict=True)
    rounds: Annotated[int, Field(ge=1, le=2, strict=True)] = 1
    confidence_threshold: Annotated[float, Field(ge=0, le=1, strict=True)] = 0.8
    levels: list[LevelIn] = Field(min_length=1, max_length=10)
    voting_timeout_minutes: Minutes = 60
    risk_floors: dict[ActionType, Risk] = Field(default_factory=dict, max_length=200)


class PolicyIn(PolicyFields):
    team_id: uuid.UUID | None = None  # None = the org default; a team needs the team_policies flag


class PolicyPatch(BaseSchema):
    """Same bounds as PolicyIn, every field optional; the team is fixed at create."""

    name: str | None = Field(None, max_length=80)
    quorum_size: Count | None = None
    require_distinct_agents: bool | None = Field(None, strict=True)
    rounds: Annotated[int, Field(ge=1, le=2, strict=True)] | None = None
    confidence_threshold: Annotated[float, Field(ge=0, le=1, strict=True)] | None = None
    levels: list[LevelIn] | None = Field(None, min_length=1, max_length=10)
    voting_timeout_minutes: Minutes | None = None
    risk_floors: dict[ActionType, Risk] | None = Field(None, max_length=200)
    # Optimistic concurrency: the updated_at the editor read; a different current value is a 409.
    if_updated_at: datetime | None = None


class PolicyOut(BaseModel):
    id: uuid.UUID
    team_id: uuid.UUID | None
    name: str
    quorum_size: int
    require_distinct_agents: bool
    rounds: int
    confidence_threshold: float
    levels: list[dict[str, Any]]
    voting_timeout_minutes: int
    risk_floors: dict[str, str]
    created_at: datetime
    updated_at: datetime


@router.post(
    "",
    status_code=201,
    response_model=PolicyOut,
    dependencies=[Depends(write_limit), Depends(TEAM_GATE)],
    responses=error_responses(402, 409),
)
async def create(
    body: PolicyIn, response: Response, admin: AdminDep, engine: EngineDep
) -> PolicyOut:
    data = body.model_dump(exclude={"team_id"})
    async with tenant_session(engine, admin.organization_id) as s:
        row = await policies.create_policy(
            s, admin.organization_id, admin.user_id, body.team_id, data
        )
    response.headers["Location"] = f"/api/v1/escalation-policies/{row['id']}"
    return PolicyOut(**row)


@router.get("", response_model=Page[PolicyOut])
async def list_policies(
    request: Request, actor: ActorDep, engine: EngineDep,
    page: Annotated[PaginationParams, Depends()],
) -> Page[PolicyOut]:  # fmt: skip
    async with tenant_session(engine, actor.organization_id) as s:
        rows, total = await policies.list_policies(s, actor.organization_id, page.skip, page.limit)
    return paginate(request, [PolicyOut(**r) for r in rows], total, page.skip, page.limit)


@router.get("/{policy_id}", response_model=PolicyOut)
async def get_policy(policy_id: uuid.UUID, actor: ActorDep, engine: EngineDep) -> PolicyOut:
    async with tenant_session(engine, actor.organization_id) as s:
        return PolicyOut(**await policies.get_policy(s, actor.organization_id, policy_id))


@router.patch(
    "/{policy_id}",
    response_model=PolicyOut,
    dependencies=[Depends(write_limit)],
    responses=error_responses(409),
)
async def update(
    policy_id: uuid.UUID, body: PolicyPatch, admin: AdminDep, engine: EngineDep
) -> PolicyOut:
    # An explicit null on a required field is a 422 from the service, never a silent keep.
    fields = body.model_dump(exclude_unset=True)
    expected = fields.pop("if_updated_at", None)
    async with tenant_session(engine, admin.organization_id) as s:
        return PolicyOut(
            **await policies.update_policy(
                s, admin.organization_id, admin.user_id, policy_id, fields, expected
            )
        )


@router.delete("/{policy_id}", status_code=204, dependencies=[Depends(write_limit)])
async def delete(policy_id: uuid.UUID, admin: AdminDep, engine: EngineDep) -> Response:
    async with tenant_session(engine, admin.organization_id) as s:
        await policies.delete_policy(s, admin.organization_id, admin.user_id, policy_id)
    return Response(status_code=204)

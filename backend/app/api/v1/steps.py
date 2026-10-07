import uuid
from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Header, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from app.api import idempotency
from app.api.deps import ActorDep, ClockDep, EngineDep, write_limit
from app.api.errors import error_responses
from app.api.pagination import Page, PaginationParams, paginate
from app.api.schemas.base import BaseSchema
from app.authz.principals import Principal, UserPrincipal
from app.config import settings
from app.db.session import tenant_session
from app.domain.errors import Forbidden
from app.domain.receipt import KeyRing
from app.services import steps

router = APIRouter(tags=["approval-steps"])


class DecideIn(BaseSchema):
    request_version: int = Field(strict=True)  # a JSON `true` is not version 1
    decision: Literal["approve", "reject"]
    comment: str = Field(default="", max_length=4096)


class DecideOut(BaseModel):
    step_id: uuid.UUID
    status: str
    request_status: str
    next_step_id: uuid.UUID | None


class PendingStepOut(BaseModel):
    id: uuid.UUID
    decision_request_id: uuid.UUID
    request_version: int
    level: int
    required_role: str
    due_at: datetime
    title: str
    effective_risk_level: str
    requested_by_agent_id: uuid.UUID | None


def require_user(actor: Principal) -> UserPrincipal:
    if not isinstance(actor, UserPrincipal):
        raise Forbidden("only a person decides approval steps")  # agents never decide human steps
    return actor


@router.post(
    "/approval-steps/{step_id}/decide",
    response_model=DecideOut,
    dependencies=[Depends(write_limit)],
    responses=error_responses(409),
)
async def decide(
    step_id: uuid.UUID,
    body: DecideIn,
    actor: ActorDep,
    engine: EngineDep,
    clock: ClockDep,
    idempotency_key: Annotated[str | None, Header()] = None,
) -> JSONResponse:
    """Approve or reject the pending step; a repeated Idempotency-Key with the same body returns the first answer."""
    user = require_user(actor)
    o, data = user.organization_id, body.model_dump()
    route = f"POST /api/v1/approval-steps/{step_id}/decide"
    async with tenant_session(engine, o) as s:
        if idempotency_key is not None:
            replay = await idempotency.begin(
                s, o, user.user_id, idempotency_key, route, data, clock
            )
            if replay:
                return JSONResponse(replay.body, status_code=replay.status)
        made = await steps.decide_step(
            s, o, clock, keys=KeyRing.parse(settings.receipt_keys), who=user, step_id=step_id,
            request_version=body.request_version, approve=body.decision == "approve",
            comment=body.comment,
        )  # fmt: skip
        out = DecideOut(**made).model_dump(mode="json")
        if idempotency_key is not None:
            await idempotency.finish(s, o, user.user_id, idempotency_key, route, 200, out)
    return JSONResponse(out)


@router.get("/me/pending-approvals", response_model=Page[PendingStepOut])
async def pending_approvals(
    request: Request,
    actor: ActorDep,
    engine: EngineDep,
    page: Annotated[PaginationParams, Depends()],
) -> Page[PendingStepOut]:
    """Steps the caller may decide right now, soonest due first."""
    user = require_user(actor)
    async with tenant_session(engine, user.organization_id) as s:
        rows = await steps.pending_for(s, user.organization_id, user.user_id)
    items = [PendingStepOut(**r) for r in rows[page.skip : page.skip + page.limit]]
    return paginate(request, items, len(rows), page.skip, page.limit)

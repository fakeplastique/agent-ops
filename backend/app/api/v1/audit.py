import uuid
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel

from app.api.deps import ActorDep, EngineDep
from app.api.pagination import Page, PaginationParams, paginate
from app.db.session import tenant_session
from app.services import audit

router = APIRouter(prefix="/audit-logs", tags=["audit"])


class AuditOut(BaseModel):
    id: uuid.UUID
    entity_type: str
    entity_id: uuid.UUID
    action: str
    actor_type: str
    actor_id: uuid.UUID | None
    data: dict[str, Any]
    created_at: datetime


@router.get("", response_model=Page[AuditOut])
async def list_audit(
    request: Request,
    actor: ActorDep,
    engine: EngineDep,
    page: Annotated[PaginationParams, Depends()],
    decision_request_id: uuid.UUID | None = None,
) -> Page[AuditOut]:
    """The caller's organization's audit trail, optionally for one request (read-only)."""
    async with tenant_session(engine, actor.organization_id) as s:
        rows, total = await audit.list_entries(
            s, actor.organization_id, decision_request_id, page.skip, page.limit
        )
    return paginate(request, [AuditOut(**r) for r in rows], total, page.skip, page.limit)

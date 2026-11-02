import uuid
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request, Response
from fastapi.responses import RedirectResponse
from pydantic import BaseModel

from app.api import gating
from app.api.deps import ActorDep, EngineDep, StorageDep, write_limit
from app.api.errors import error_responses
from app.api.pagination import Page, PaginationParams, paginate
from app.authz.principals import AgentPrincipal, Principal
from app.db.session import tenant_session
from app.services import attachments
from app.services.attachments import Uploader

ATTACHMENTS_GATE = gating.gate("attachments")
router = APIRouter(
    prefix="/decision-requests/{decision_request_id}/attachments",
    tags=["attachments"],
    dependencies=[
        Depends(ATTACHMENTS_GATE)
    ],  # first, so a Free plan hears 402 before any role or id check
)


class AttachmentOut(BaseModel):
    id: uuid.UUID
    decision_request_id: uuid.UUID
    filename: str
    content_type: str
    size_bytes: int
    uploaded_by_type: str
    uploaded_by_id: uuid.UUID
    created_at: datetime


def uploader_of(actor: Principal) -> Uploader:
    if isinstance(actor, AgentPrincipal):
        return Uploader("agent", actor.agent_id)
    return Uploader("user", actor.user_id, actor.role)


@router.post(
    "",
    status_code=201,
    response_model=AttachmentOut,
    dependencies=[Depends(write_limit)],
    responses=error_responses(413, 415, 503),
    openapi_extra={
        "requestBody": {
            "required": True,
            "content": {
                "application/octet-stream": {"schema": {"type": "string", "format": "binary"}}
            },
        }
    },
)
async def upload(
    decision_request_id: uuid.UUID,
    request: Request,
    filename: Annotated[
        str, Query(min_length=1, max_length=1024, description="Shown, never a path")
    ],
    actor: ActorDep,
    engine: EngineDep,
    storage: StorageDep,
) -> AttachmentOut:
    """The request body is the file (up to 10 MB), its Content-Type the declared type. Allowed: pdf, png,
    jpg, gif, webp, txt, csv, json, docx, xlsx; the first bytes must match the type."""
    row = await attachments.upload(
        engine, storage, actor.organization_id, request_id=decision_request_id, filename=filename,
        declared_type=request.headers.get("content-type"), chunks=request.stream(),
        by=uploader_of(actor),
    )  # fmt: skip
    return AttachmentOut(**row)


@router.get("", response_model=Page[AttachmentOut])
async def list_attachments(
    decision_request_id: uuid.UUID,
    request: Request,
    actor: ActorDep,
    engine: EngineDep,
    page: Annotated[PaginationParams, Depends()],
) -> Page[AttachmentOut]:
    async with tenant_session(engine, actor.organization_id) as s:
        rows, total = await attachments.list_attachments(
            s, actor.organization_id, decision_request_id, page.skip, page.limit
        )
    return paginate(request, [AttachmentOut(**r) for r in rows], total, page.skip, page.limit)


@router.get("/{attachment_id}/download", status_code=307, response_class=RedirectResponse)
async def download(
    decision_request_id: uuid.UUID,
    attachment_id: uuid.UUID,
    actor: ActorDep,
    engine: EngineDep,
    storage: StorageDep,
) -> RedirectResponse:
    """Same authorization as reading the request, then a redirect to a 5-minute signed download URL."""
    async with tenant_session(engine, actor.organization_id) as s:
        url = await attachments.download_url(
            s, storage, actor.organization_id, decision_request_id, attachment_id
        )
    return RedirectResponse(url, status_code=307, headers={"Cache-Control": "no-store"})


@router.delete("/{attachment_id}", status_code=204, dependencies=[Depends(write_limit)])
async def remove(
    decision_request_id: uuid.UUID,
    attachment_id: uuid.UUID,
    actor: ActorDep,
    engine: EngineDep,
    storage: StorageDep,
) -> Response:
    """The uploader or an org_admin removes the file."""
    async with tenant_session(engine, actor.organization_id) as s:
        key = await attachments.remove(
            s, actor.organization_id, decision_request_id, attachment_id, uploader_of(actor)
        )
    await attachments.drop_object(
        storage, key
    )  # after the commit: a rolled-back delete keeps its file
    return Response(status_code=204)

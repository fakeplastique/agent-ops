import uuid
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, Field

from app.api.deps import ActorDep, EngineDep, SessionUserDep, user_write_limit, write_limit
from app.api.pagination import Page, PaginationParams, paginate
from app.api.schemas.base import BaseSchema
from app.authz.origin import require_same_origin
from app.authz.principals import UserPrincipal
from app.db.session import tenant_session
from app.domain.errors import Forbidden
from app.services import tokens

router = APIRouter(prefix="/me/tokens", tags=["me"])


def require_user(actor: ActorDep) -> UserPrincipal:
    if not isinstance(actor, UserPrincipal):
        raise Forbidden("personal access tokens belong to people, not agents")
    return actor


UserDep = Annotated[UserPrincipal, Depends(require_user)]


class PatIn(BaseSchema):
    name: str = Field(max_length=80)


class PatOut(BaseModel):
    id: uuid.UUID
    name: str
    created_at: datetime
    revoked_at: datetime | None


class PatCreated(PatOut):
    token: str  # shown once


@router.post(
    "",
    status_code=201,
    response_model=PatCreated,
    dependencies=[Depends(require_same_origin), Depends(user_write_limit)],
)
async def create(
    body: PatIn, response: Response, user: SessionUserDep, engine: EngineDep
) -> PatCreated:
    """Web session only, so a leaked token cannot mint its own successors. The raw value is only in this reply."""
    async with tenant_session(engine, user.organization_id) as s:
        pat_id, raw = await tokens.create_pat(s, user.organization_id, user.user_id, body.name)
        row = await tokens.get_pat(s, user.organization_id, user.user_id, pat_id)
    response.headers["Location"] = f"/api/v1/me/tokens/{pat_id}"
    return PatCreated(**row, token=raw)


@router.get("", response_model=Page[PatOut])
async def list_tokens(
    request: Request, user: UserDep, engine: EngineDep,
    page: Annotated[PaginationParams, Depends()],
) -> Page[PatOut]:  # fmt: skip
    async with tenant_session(engine, user.organization_id) as s:
        rows, total = await tokens.list_pats(
            s, user.organization_id, user.user_id, page.skip, page.limit
        )
    return paginate(request, [PatOut(**r) for r in rows], total, page.skip, page.limit)


@router.get("/{token_id}", response_model=PatOut)
async def get_token(token_id: uuid.UUID, user: UserDep, engine: EngineDep) -> PatOut:
    async with tenant_session(engine, user.organization_id) as s:
        return PatOut(**await tokens.get_pat(s, user.organization_id, user.user_id, token_id))


@router.delete("/{token_id}", status_code=204, dependencies=[Depends(write_limit)])
async def revoke(token_id: uuid.UUID, user: UserDep, engine: EngineDep) -> Response:
    async with tenant_session(engine, user.organization_id) as s:
        await tokens.revoke_pat(s, user.organization_id, token_id, user.user_id)
    return Response(status_code=204)

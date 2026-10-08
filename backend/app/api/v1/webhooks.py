import uuid
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, Field, StrictBool

from app.api.deps import AdminDep, EngineDep, write_limit
from app.api.pagination import Page, PaginationParams, paginate
from app.api.schemas.base import BaseSchema
from app.db.session import tenant_session
from app.services import webhooks
from app.services.webhook_security import Resolver, system_resolve

# Webhook config is admin data (a URL can carry the receiver's own token): reads are org_admin only too.
router = APIRouter(prefix="/webhook-subscriptions", tags=["webhooks"])


def get_resolver() -> Resolver:
    return system_resolve  # tests override this with a fake DNS


ResolverDep = Annotated[Resolver, Depends(get_resolver)]


class WebhookIn(BaseSchema):
    url: str = Field(max_length=4096)  # the guard's own 2048 cap answers with its rule name
    events: list[str] = Field(min_length=1, max_length=50)


class WebhookPatch(BaseSchema):
    url: str | None = Field(None, max_length=4096)
    events: list[str] | None = Field(None, min_length=1, max_length=50)
    active: StrictBool | None = None


class WebhookOut(BaseModel):
    id: uuid.UUID
    url: str
    events: list[str]
    active: bool
    created_at: datetime
    updated_at: datetime


class WebhookCreated(WebhookOut):
    secret: str  # shown once: only this reply and rotate-secret ever carry it


class WebhookSecret(BaseModel):
    id: uuid.UUID
    secret: str  # shown once


@router.post(
    "", status_code=201, response_model=WebhookCreated, dependencies=[Depends(write_limit)]
)
async def create(
    body: WebhookIn, response: Response, admin: AdminDep, engine: EngineDep, resolver: ResolverDep
) -> WebhookCreated:
    async with tenant_session(engine, admin.organization_id) as s:
        row, secret = await webhooks.create_subscription(
            s, admin.organization_id, admin.user_id, body.url, body.events, resolver
        )
    response.headers["Location"] = f"/api/v1/webhook-subscriptions/{row['id']}"
    return WebhookCreated(**row, secret=secret)


@router.get("", response_model=Page[WebhookOut])
async def list_subscriptions(
    request: Request, admin: AdminDep, engine: EngineDep,
    page: Annotated[PaginationParams, Depends()],
) -> Page[WebhookOut]:  # fmt: skip
    async with tenant_session(engine, admin.organization_id) as s:
        rows, total = await webhooks.list_subscriptions(
            s, admin.organization_id, page.skip, page.limit
        )
    return paginate(request, [WebhookOut(**r) for r in rows], total, page.skip, page.limit)


@router.get("/{subscription_id}", response_model=WebhookOut)
async def get_subscription(
    subscription_id: uuid.UUID, admin: AdminDep, engine: EngineDep
) -> WebhookOut:
    async with tenant_session(engine, admin.organization_id) as s:
        return WebhookOut(
            **await webhooks.get_subscription(s, admin.organization_id, subscription_id)
        )


@router.patch("/{subscription_id}", response_model=WebhookOut, dependencies=[Depends(write_limit)])
async def update(
    subscription_id: uuid.UUID, body: WebhookPatch, admin: AdminDep, engine: EngineDep,
    resolver: ResolverDep,
) -> WebhookOut:  # fmt: skip
    fields = body.model_dump(exclude_unset=True)  # only what was sent; an explicit null is refused
    async with tenant_session(engine, admin.organization_id) as s:
        return WebhookOut(
            **await webhooks.update_subscription(
                s, admin.organization_id, admin.user_id, subscription_id, fields, resolver
            )
        )


@router.post(
    "/{subscription_id}/rotate-secret",
    response_model=WebhookSecret,
    dependencies=[Depends(write_limit)],
)
async def rotate_secret(
    subscription_id: uuid.UUID, admin: AdminDep, engine: EngineDep
) -> WebhookSecret:
    async with tenant_session(engine, admin.organization_id) as s:
        secret = await webhooks.rotate_secret(
            s, admin.organization_id, admin.user_id, subscription_id
        )
    return WebhookSecret(id=subscription_id, secret=secret)


@router.delete("/{subscription_id}", status_code=204, dependencies=[Depends(write_limit)])
async def delete(subscription_id: uuid.UUID, admin: AdminDep, engine: EngineDep) -> Response:
    async with tenant_session(engine, admin.organization_id) as s:
        await webhooks.delete_subscription(s, admin.organization_id, admin.user_id, subscription_id)
    return Response(status_code=204)

import uuid
from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, Field, StrictBool

from app.api.deps import AdminDep, ClockDep, EngineDep, write_limit
from app.api.pagination import Page, PaginationParams, paginate
from app.api.schemas.base import BaseSchema
from app.db.session import tenant_session
from app.services import webhooks
from app.services.webhook_security import Resolver, system_resolve, validate_url

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
    # DNS runs before the session: a slow resolver must not hold a pooled transaction open.
    url = await validate_url(body.url, resolver=resolver)
    async with tenant_session(engine, admin.organization_id) as s:
        row, secret = await webhooks.create_subscription(
            s, admin.organization_id, admin.user_id, url, body.events
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
    if fields.get("url") is not None:  # same guard as create, before the session
        fields["url"] = await validate_url(fields["url"], resolver=resolver)
    async with tenant_session(engine, admin.organization_id) as s:
        return WebhookOut(
            **await webhooks.update_subscription(
                s, admin.organization_id, admin.user_id, subscription_id, fields
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


class DeliveryOut(BaseModel):
    id: uuid.UUID  # also the webhook-id the receiver saw
    event_id: uuid.UUID
    event_type: str
    status: Literal["pending", "delivered", "dead"]
    attempt: int
    next_attempt_at: datetime | None
    last_status_code: int | None
    last_error: str | None  # a short reason, never a response body
    created_at: datetime
    updated_at: datetime


@router.get("/{subscription_id}/deliveries", response_model=Page[DeliveryOut])
async def list_deliveries(
    subscription_id: uuid.UUID, request: Request, admin: AdminDep, engine: EngineDep,
    page: Annotated[PaginationParams, Depends()],
) -> Page[DeliveryOut]:  # fmt: skip
    async with tenant_session(engine, admin.organization_id) as s:
        rows, total = await webhooks.list_deliveries(
            s, admin.organization_id, subscription_id, page.skip, page.limit
        )
    return paginate(request, [DeliveryOut(**r) for r in rows], total, page.skip, page.limit)


@router.post(
    "/{subscription_id}/deliveries/{delivery_id}/redeliver",
    status_code=202,
    response_model=DeliveryOut,
    dependencies=[Depends(write_limit)],
)
async def redeliver(
    subscription_id: uuid.UUID, delivery_id: uuid.UUID, admin: AdminDep, engine: EngineDep,
    clock: ClockDep,
) -> DeliveryOut:  # fmt: skip
    # Queued only: the worker's retry sweep sends it, so this request makes no outbound call.
    async with tenant_session(engine, admin.organization_id) as s:
        row = await webhooks.redeliver(
            s, admin.organization_id, admin.user_id, subscription_id, delivery_id, clock.now()
        )
    return DeliveryOut(**row)


@router.delete("/{subscription_id}", status_code=204, dependencies=[Depends(write_limit)])
async def delete(subscription_id: uuid.UUID, admin: AdminDep, engine: EngineDep) -> Response:
    async with tenant_session(engine, admin.organization_id) as s:
        await webhooks.delete_subscription(s, admin.organization_id, admin.user_id, subscription_id)
    return Response(status_code=204)

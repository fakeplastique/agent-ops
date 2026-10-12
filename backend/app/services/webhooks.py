"""Webhook subscriptions (S27): the secret is sealed at rest; URLs arrive already through validate_url.

The plaintext secret leaves only in the create and rotate replies; nothing here logs or audits it."""

import uuid
from collections.abc import Iterable
from datetime import datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.domain.errors import InvalidInput, NotFoundError, SubscriptionPaused
from app.domain.secretbox import SecretBox
from app.services import audit, webhook_security

# What a subscriber can select: every type the engine emits (a test greps the emits against this set).
EVENT_TYPES = frozenset({
    "decision.created", "decision.status_changed", "decision.revised", "vote.cast", "round.revealed",
    "step.created", "step.decided", "step.timed_out", "message.posted", "plan.changed",
    "attachment.added",
})  # fmt: skip
MAX_SUBSCRIPTIONS = 20  # per organization: each one is a fan-out of outbound requests per event

_COLS = "id, url, events, active, created_at, updated_at"  # never secret_ciphertext
_LOCK = text("select pg_advisory_xact_lock(hashtextextended(:k, 0))")
_COUNT = text("select count(*) from webhook_subscriptions where organization_id = :o")
_INSERT = text(
    "insert into webhook_subscriptions (id, organization_id, url, events, secret_ciphertext)"
    f" values (:i, :o, :u, :e, :c) returning {_COLS}"
)
_LIST = text(
    f"select {_COLS}, count(*) over () as total from webhook_subscriptions where organization_id = :o"
    " order by created_at, id offset :skip limit :limit"
)
_ONE = text(f"select {_COLS} from webhook_subscriptions where organization_id = :o and id = :i")
_UPDATE = text(
    "update webhook_subscriptions set url = coalesce(:u, url), events = coalesce(:e, events),"
    " active = coalesce(:a, active), updated_at = now()"
    f" where organization_id = :o and id = :i returning {_COLS}"
)
_ROTATE = text(
    "update webhook_subscriptions set secret_ciphertext = :c, updated_at = now()"
    " where organization_id = :o and id = :i returning id"
)
_DELETE = text(
    "delete from webhook_subscriptions where organization_id = :o and id = :i returning id"
)
_SECRET = text(
    "select secret_ciphertext from webhook_subscriptions where organization_id = :o and id = :i"
)


def _aad(org: uuid.UUID, sub: uuid.UUID) -> bytes:
    # Binds the ciphertext to its tenant and row: copied onto another subscription it does not open.
    return f"webhook_subscription:{org}:{sub}".encode()


def _seal(org: uuid.UUID, sub: uuid.UUID) -> tuple[str, str]:
    secret = webhook_security.new_secret()
    return secret, SecretBox.parse(settings.webhook_secret_keys).seal(secret, _aad(org, sub))


def _events(events: Any) -> list[str]:
    ok = isinstance(events, list) and events and all(isinstance(e, str) for e in events)
    if not ok or not set(events) <= EVENT_TYPES:
        raise InvalidInput(f"events must be a non-empty list of: {', '.join(sorted(EVENT_TYPES))}")
    return sorted(set(events))


async def create_subscription(
    s: AsyncSession, org: uuid.UUID, actor: uuid.UUID, url: str, events: Iterable[str]
) -> tuple[dict, str]:
    """The new subscription and its secret (shown once). `url` is validate_url's output."""
    events = _events(events)
    # Serialise creates per org: two cannot both count below the cap.
    await s.execute(_LOCK, {"k": f"webhook_subscriptions:{org}"})
    if (await s.execute(_COUNT, {"o": org})).scalar_one() >= MAX_SUBSCRIPTIONS:
        raise InvalidInput(f"an organization has at most {MAX_SUBSCRIPTIONS} webhook subscriptions")
    sub = uuid.uuid4()
    secret, sealed = _seal(org, sub)
    params = {"i": sub, "o": org, "u": url, "e": events, "c": sealed}
    row = (await s.execute(_INSERT, params)).mappings().one()
    await audit.record(
        s, org, "webhook_subscription", sub, "created", "user", actor, {"events": events}
    )
    return dict(row), secret


async def list_subscriptions(
    s: AsyncSession, org: uuid.UUID, skip: int, limit: int
) -> tuple[list[dict], int]:
    rows = (await s.execute(_LIST, {"o": org, "skip": skip, "limit": limit})).mappings().all()
    return [dict(r) for r in rows], (rows[0]["total"] if rows else 0)


async def get_subscription(s: AsyncSession, org: uuid.UUID, sub: uuid.UUID) -> dict:
    row = (await s.execute(_ONE, {"o": org, "i": sub})).mappings().first()
    if row is None:
        raise NotFoundError
    return dict(row)


async def update_subscription(
    s: AsyncSession, org: uuid.UUID, actor: uuid.UUID, sub: uuid.UUID, fields: dict[str, Any]
) -> dict:
    """`fields` holds only what the caller sent; a new `url` is validate_url's output."""
    if any(v is None for v in fields.values()):
        raise InvalidInput("url, events and active cannot be null")
    url = fields.get("url")
    events = _events(fields["events"]) if "events" in fields else None
    active = fields.get("active")
    if active is not None and not isinstance(active, bool):
        raise InvalidInput("active must be a boolean")
    params = {"u": url, "e": events, "a": active, "o": org, "i": sub}
    row = (await s.execute(_UPDATE, params)).mappings().first()
    if row is None:
        raise NotFoundError
    await audit.record(
        s, org, "webhook_subscription", sub, "updated", "user", actor, {"fields": sorted(fields)}
    )
    return dict(row)


async def rotate_secret(s: AsyncSession, org: uuid.UUID, actor: uuid.UUID, sub: uuid.UUID) -> str:
    """A new secret (shown once); the old one stops signing in the same transaction."""
    secret, sealed = _seal(org, sub)
    if (await s.execute(_ROTATE, {"c": sealed, "o": org, "i": sub})).first() is None:
        raise NotFoundError
    await audit.record(s, org, "webhook_subscription", sub, "secret_rotated", "user", actor)
    return secret


async def delete_subscription(
    s: AsyncSession, org: uuid.UUID, actor: uuid.UUID, sub: uuid.UUID
) -> None:
    if (await s.execute(_DELETE, {"o": org, "i": sub})).first() is None:
        raise NotFoundError
    await audit.record(s, org, "webhook_subscription", sub, "deleted", "user", actor)


_DCOLS = (
    "id, event_id, event_type, status, attempt, next_attempt_at, last_status_code, last_error,"
    " created_at, updated_at"
)
_DELIVERIES = text(
    f"select {_DCOLS}, count(*) over () as total from webhook_deliveries"
    " where organization_id = :o and subscription_id = :s"
    " order by created_at desc, id offset :skip limit :limit"
)
_DELIVERY = text(
    f"select {_DCOLS} from webhook_deliveries"
    " where organization_id = :o and subscription_id = :s and id = :d"
)
# Same row, so the same webhook-id; a pending row is already queued, and a paused hook queues nothing.
_REDELIVER = text(
    "update webhook_deliveries d set status = 'pending', attempt = 0, next_attempt_at = :now,"
    " last_status_code = null, last_error = null, updated_at = now() from webhook_subscriptions s"
    " where s.organization_id = :o and s.id = d.subscription_id and s.active"
    " and d.organization_id = :o and d.subscription_id = :s and d.id = :d and d.status <> 'pending'"
    " returning d.id"
)


async def list_deliveries(
    s: AsyncSession, org: uuid.UUID, sub: uuid.UUID, skip: int, limit: int
) -> tuple[list[dict], int]:
    # 404 for a foreign or deleted subscription, not an empty page
    await get_subscription(s, org, sub)
    params = {"o": org, "s": sub, "skip": skip, "limit": limit}
    rows = (await s.execute(_DELIVERIES, params)).mappings().all()
    return [dict(r) for r in rows], (rows[0]["total"] if rows else 0)


async def redeliver(
    s: AsyncSession,
    org: uuid.UUID,
    actor: uuid.UUID,
    sub: uuid.UUID,
    delivery: uuid.UUID,
    now: datetime,
) -> dict:
    """Queue a finished delivery again with 5 fresh attempts; the sweep sends it, not this call."""
    params = {"o": org, "s": sub, "d": delivery, "now": now}
    queued = (await s.execute(_REDELIVER, params)).first() is not None
    if queued:
        await audit.record(s, org, "webhook_delivery", delivery, "redelivered", "user", actor)
    row = (await s.execute(_DELIVERY, params)).mappings().first()
    if row is None:
        raise NotFoundError
    if not queued and not (await get_subscription(s, org, sub))["active"]:
        raise SubscriptionPaused  # 409, not a 202 the claim would then kill
    return dict(row)


async def load_secret(s: AsyncSession, org: uuid.UUID, sub: uuid.UUID) -> str:
    """The plaintext secret, for signing a delivery (S28) only: never returned by any read route."""
    sealed = (await s.execute(_SECRET, {"o": org, "i": sub})).scalar_one_or_none()
    if sealed is None:
        raise NotFoundError
    return SecretBox.parse(settings.webhook_secret_keys).open(sealed, _aad(org, sub))

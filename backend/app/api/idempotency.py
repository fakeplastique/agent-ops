import hashlib
import json
import uuid
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.errors import IdempotencyKeyReuse, InvalidInput
from app.ports.clock import Clock

TTL = timedelta(hours=24)

# An expired row is dropped so its key can be claimed afresh.
_EXPIRE = text(
    "delete from idempotency_keys where organization_id=:o and principal_id=:p and key=:k"
    " and route=:r and created_at <= :cutoff"
)
# The claim is an insert in the caller's own transaction: a concurrent twin blocks on the primary key until
# this one commits (then conflicts and replays) or rolls back (then wins). No check-then-insert gap.
_CLAIM = text(
    "insert into idempotency_keys (organization_id, principal_id, key, route, request_hash, created_at)"
    " values (:o, :p, :k, :r, :h, :now) on conflict do nothing returning 1"
)
_READ = text(
    "select request_hash, response_status, response_body from idempotency_keys"
    " where organization_id=:o and principal_id=:p and key=:k and route=:r"
)
_FINISH = text(
    "update idempotency_keys set response_status=:s, response_body=cast(:b as jsonb)"
    " where organization_id=:o and principal_id=:p and key=:k and route=:r"
)


@dataclass(frozen=True)
class Replay:
    status: int
    body: Any


def _check_key(key: str) -> None:
    if not 1 <= len(key) <= 255 or not key.isprintable():
        raise InvalidInput("Idempotency-Key must be 1-255 printable characters")


def _hash(body: Any) -> str:
    canonical = json.dumps(body, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode()).hexdigest()


async def begin(
    session: AsyncSession,
    org: uuid.UUID,
    principal_id: uuid.UUID,
    key: str,
    route: str,
    body: Any,
    clock: Clock,
) -> Replay | None:
    """Claim the key (None: go ahead and call `finish`) or return the first answer. Same key, other body: 422.

    `route` is the concrete method + path (ids included), not the template. A 201 replay must resend Location."""
    _check_key(key)
    now = clock.now()
    ids = {"o": org, "p": principal_id, "k": key, "r": route}
    await session.execute(_EXPIRE, {**ids, "cutoff": now - TTL})
    h = _hash(body)
    if (await session.execute(_CLAIM, {**ids, "h": h, "now": now})).first():
        return None
    stored_hash, status, answer = (await session.execute(_READ, ids)).one()
    if status is None:  # committed without `finish`: a handler bug, never a replayable answer
        raise RuntimeError("idempotency key claimed but never finished")
    if stored_hash != h:
        raise IdempotencyKeyReuse
    return Replay(status, answer)


async def finish(
    session: AsyncSession,
    org: uuid.UUID,
    principal_id: uuid.UUID,
    key: str,
    route: str,
    status: int,
    body: Any,
) -> None:
    """Store the answer in the same transaction as the effect, so a replay sees both or neither."""
    ids = {"o": org, "p": principal_id, "k": key, "r": route}
    await session.execute(_FINISH, {**ids, "s": status, "b": json.dumps(body, default=str)})

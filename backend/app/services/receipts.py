import uuid
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.db.session import lookup_as, use_tenant
from app.domain.errors import InvalidInput, NotFoundError
from app.domain.hashing import content_hash
from app.domain.receipt import KeyRing, Subject, verify
from app.domain.statuses import Status

_RECEIPT = text(
    "select receipt from decision_requests where organization_id = :o and id = :r"
    " and status = 'approved'"
)
_FIND = text("select organization_id from decision_requests where id = :r")
_LIVE = text(
    "select status, version, title, action_type, payload, risk_level, decided_at"
    " from decision_requests where organization_id = :o and id = :r"
)
_APPROVERS = text(
    "select approver_user_id from approval_steps where organization_id = :o"
    " and decision_request_id = :r and request_version = :v and status = 'approved'"
)


async def get_receipt(session: AsyncSession, o: uuid.UUID, r: uuid.UUID) -> dict[str, Any]:
    """The stored receipt; only an approved request has one (FM15)."""
    receipt = (await session.execute(_RECEIPT, {"o": o, "r": r})).scalar()
    if receipt is None:
        raise NotFoundError
    return receipt


async def verify_receipt(engine: AsyncEngine, keys: KeyRing, receipt: Any) -> bool:
    """Whether the receipt still describes its live request. Public: the answer is only a boolean.

    The subject is rebuilt from the live row (content hash recomputed, approvers from the steps), never from
    the stored hash or receipt, so a change made behind the app's back breaks it (I12)."""
    try:
        rid = uuid.UUID(receipt["payload"]["request_id"])
    except (TypeError, KeyError, ValueError, AttributeError):
        return False
    async with engine.begin() as c:
        await lookup_as(c, "app.receipt_request_id", str(rid))  # an approved row only, any tenant
        org = (await c.execute(_FIND, {"r": rid})).scalar()
        if org is None:
            return False
        await use_tenant(c, org)
        live = (await c.execute(_LIVE, {"o": org, "r": rid})).mappings().one()
        rows = await c.execute(_APPROVERS, {"o": org, "r": rid, "v": live["version"]})
        approvers = tuple(a for (a,) in rows)
    try:
        digest = content_hash(
            live["title"], live["action_type"], live["payload"], live["risk_level"]
        )
        subject = Subject(rid, live["version"], digest, live["decided_at"], approvers or "auto")
    except InvalidInput:  # a tampered row that no longer forms a subject
        return False
    return verify(receipt, subject, Status(live["status"]), keys)

import json
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.errors import IllegalTransitionError
from app.domain.statuses import Status
from app.services import audit, outbox

_SET_STATUS = text(
    "update decision_requests set status = :new, updated_at = :now, decided_at = :decided,"
    " receipt = cast(:receipt as jsonb) where organization_id = :o and id = :r and status = :old"
    " returning id"
)


async def change_status(
    session: AsyncSession,
    organization_id: uuid.UUID,
    request_id: uuid.UUID,
    version: int,
    old: Status,
    new: Status,
    now: datetime,
    receipt: dict[str, Any] | None,
    actor_type: str,
    actor_id: uuid.UUID | None,
) -> None:
    """The one place a request changes status: conditional update, audit row and outbox row together.

    Callers hold the request's row lock; the conditional WHERE is the second guard (never double-apply)."""
    decided = now if new in (Status.APPROVED, Status.REJECTED) else None
    result = await session.execute(
        _SET_STATUS,
        {
            "o": organization_id, "r": request_id, "old": old.value, "new": new.value, "now": now,
            "decided": decided, "receipt": json.dumps(receipt) if receipt else None,
        },
    )  # fmt: skip
    if result.first() is None:
        raise IllegalTransitionError("the request changed under the operation")
    change = {"from": old.value, "to": new.value, "version": version}
    await audit.record(
        session, organization_id, "decision_request", request_id, "status_changed",
        actor_type, actor_id, change | {"receipt": receipt is not None},
    )  # fmt: skip
    await outbox.emit(
        session, organization_id, "decision.status_changed", change | {"id": str(request_id)}
    )

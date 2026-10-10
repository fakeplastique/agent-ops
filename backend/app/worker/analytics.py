"""The `analytics` outbox consumer: four domain events become Analytics events with ids and enums only."""

import uuid
from collections.abc import Mapping
from typing import Any

from sqlalchemy import text

from app.db.session import tenant_session
from app.domain.statuses import Risk, Status
from app.ports.analytics import Prop
from app.services.plans import PLANS

ENUMS = frozenset({*(s.value for s in Status), *(r.name.lower() for r in Risk), *PLANS})
DEDUP_TTL = 7 * 24 * 3600  # far past arq's 1 h job-id memory and any relay re-send
IN_FLIGHT_TTL = 60  # a job killed mid-emit leaves this marker only briefly, then a retry emits
_READ = text("select type, payload from outbox_events where organization_id = :o and id = :e")


def _is_uuid(value: str) -> bool:
    try:
        return str(uuid.UUID(value)) == value
    except ValueError:
        return False


def check_props(props: Mapping[str, Any]) -> None:
    """Refuse anything but ids, known enums and integers: free text could carry PII."""
    for key, value in props.items():
        ok = value is None or type(value) is int  # bool is an int subclass: refused
        ok = ok or (isinstance(value, str) and (value in ENUMS or _is_uuid(value)))
        if not ok:
            raise ValueError(f"analytics prop {key!r} is not an id, enum or integer")


def to_event(type_: str, p: dict[str, Any]) -> tuple[str, dict[str, Prop]] | None:
    """Pick named fields only; the outbox payload is never passed through."""
    if type_ == "decision.created":
        return "decision_created", {
            "decision_request_id": p["id"], "risk_level": p["effective_risk_level"],
        }  # fmt: skip
    # Only a closed round reaches approved from voting, and only for low risk (I6): that is auto-approval.
    if (
        type_ == "decision.status_changed"
        and p["to"] == "approved"
        and p["from"] != "pending_approval"
    ):
        return "decision_auto_approved", {"decision_request_id": p["id"]}
    if type_ == "step.timed_out":
        return "step_escalated", {
            "decision_request_id": p["decision_request_id"], "step_id": p["id"], "level": p["level"],
        }  # fmt: skip
    if type_ == "plan.changed":
        return "plan_changed", {"from": p["from"], "to": p["to"]}
    return None


async def analytics_job(ctx: dict[str, Any], organization_id: str, event_id: str) -> str:
    """Read the event inside its own tenant, map it, emit once per event id (registry.py contract)."""
    o, e = uuid.UUID(organization_id), uuid.UUID(event_id)
    async with tenant_session(ctx["work_engine"], o) as s:
        row = (await s.execute(_READ, {"o": o, "e": e})).first()
    if row is None:
        return "missing"
    mapped = to_event(row.type, row.payload)
    if mapped is None:
        return "skipped"
    name, props = mapped
    props = {"event_id": str(e), "organization_id": str(o)} | props
    check_props(props)
    # The effect is outside the database, so the marker lives in Redis: claimed briefly, kept long only once
    # the emit returned, removed when it raised.
    key = f"analytics:done:{e}"
    if not await ctx["redis"].set(key, "1", nx=True, ex=IN_FLIGHT_TTL):
        return "duplicate"
    try:
        ctx["analytics"].emit(name, props)
    except Exception:
        await ctx["redis"].delete(key)
        raise
    await ctx["redis"].expire(key, DEDUP_TTL)
    return name

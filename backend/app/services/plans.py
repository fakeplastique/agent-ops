import uuid
from datetime import UTC
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz.matrix import Action, can
from app.authz.principals import Principal, UserPrincipal
from app.domain.errors import Forbidden, NotFoundError, PlanFeatureRequired
from app.ports.clock import Clock
from app.services import audit, features, outbox

PLANS = (
    "free",
    "pro",
    "enterprise",
)  # cheapest first: the first plan with a flag is the one to name

_FLAG_PLANS = text("select plan_key from plan_features where flag_key = :f")
_FLAGS = text("select key from feature_flags order by key")
_PLAN = text(
    "select p.key, p.requests_per_month, p.max_quorum, p.max_rounds, p.max_levels"
    " from organizations o join plans p on p.key = o.plan_key where o.id = :o"
)
_USED = text(
    "select value from usage_counters where organization_id = :o and metric = 'requests'"
    " and period = :p"
)
_SET = text("select set_organization_plan(:o, :p)")  # migration 0015: current tenant only
_EMAIL = text("select email from users where id = :u")


async def required_plan(session: AsyncSession, flag: str) -> str:
    have = set((await session.execute(_FLAG_PLANS, {"f": flag})).scalars())
    return next((p for p in PLANS if p in have), PLANS[-1])


async def require_flag(session: AsyncSession, organization_id: uuid.UUID, flag: str) -> None:
    """PlanFeatureRequired naming the flag and the cheapest plan with it, unless the flag is on."""
    if not await features.is_enabled(session, organization_id, flag):
        raise PlanFeatureRequired(flag, await required_plan(session, flag))


async def read_plan(session: AsyncSession, organization_id: uuid.UUID, clock: Clock) -> dict:
    row = (await session.execute(_PLAN, {"o": organization_id})).first()
    if row is None:
        raise NotFoundError
    period = clock.now().astimezone(UTC).strftime("%Y-%m")
    used = await session.scalar(_USED, {"o": organization_id, "p": period}) or 0
    keys = (await session.execute(_FLAGS)).scalars()
    return {
        "plan": row.key,
        "limits": {
            "requests_per_month": row.requests_per_month,
            "max_quorum": row.max_quorum,
            "max_rounds": row.max_rounds,
            "max_levels": row.max_levels,
        },
        "usage": {"requests": {"used": used, "limit": row.requests_per_month, "period": period}},
        # Each flag through is_enabled (I15): the page shows exactly what the gates decide.
        "flags": [k for k in keys if await features.is_enabled(session, organization_id, k)],
    }


async def change_plan(
    session: AsyncSession, organization_id: uuid.UUID, plan: str, actor_id: uuid.UUID, via: str
) -> None:
    """Set the plan in this tenant's transaction, with its audit row and plan.changed event.

    Flags are read live on every request (no cache until S26), so the next request sees the new plan."""
    old = await session.scalar(_SET, {"o": organization_id, "p": plan})
    if old is None:
        raise NotFoundError
    if old == plan:
        return
    change: dict[str, Any] = {"from": old, "to": plan}
    await audit.record(
        session, organization_id, "organization", organization_id, "plan_changed", "user",
        actor_id, change | {"via": via},
    )  # fmt: skip
    await outbox.emit(
        session, organization_id, "plan.changed", {"organization_id": str(organization_id)} | change
    )


async def switch_own_plan(
    session: AsyncSession, actor: Principal, plan: str, demo_mode: bool
) -> None:
    """The demo switch: an org admin changes their own organization's plan, only in demo mode."""
    if not demo_mode:
        raise Forbidden("plan changes are made by a platform admin")
    if not isinstance(actor, UserPrincipal) or not can(actor.role, Action.CHANGE_PLAN):
        raise Forbidden
    await change_plan(session, actor.organization_id, plan, actor.user_id, "demo")


def allow_list(raw: str) -> frozenset[str]:
    return frozenset(e.strip().lower() for e in raw.split(",") if e.strip())


async def require_platform_admin(
    session: AsyncSession, actor: Principal, raw_list: str
) -> UserPrincipal:
    """A person whose email is on PLATFORM_ADMIN_EMAILS; read in the caller's own tenant."""
    if not isinstance(actor, UserPrincipal):
        raise Forbidden
    email = await session.scalar(_EMAIL, {"u": actor.user_id})
    if email is None or email.lower() not in allow_list(raw_list):
        raise Forbidden
    return actor

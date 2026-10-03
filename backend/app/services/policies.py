import uuid
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.errors import PolicyExceedsPlan, PolicyUnresolved

# D16: the owner's team, then each ancestor (nearest first), then the org default. Explicit org predicates
# on every table (RLS is the second wall); the path array stops a cyclic parent chain.
_RESOLVE = text(
    """
    with recursive chain as (
        select t.id, t.parent_team_id, 0 as depth, array[t.id] as path
        from memberships m
        join teams t on t.organization_id = m.organization_id and t.id = m.team_id
        where m.organization_id = :o and m.user_id = :u
        union all
        select p.id, p.parent_team_id, c.depth + 1, c.path || p.id
        from chain c
        join teams p on p.organization_id = :o and p.id = c.parent_team_id
        where p.id <> all(c.path)
    )
    select ep.id, ep.team_id, ep.quorum_size, ep.require_distinct_agents, ep.rounds,
           ep.confidence_threshold, ep.levels, ep.voting_timeout_minutes, ep.risk_floors
    from escalation_policies ep
    left join chain c on c.id = ep.team_id
    where ep.organization_id = :o and (ep.team_id is null or c.id is not null)
    order by c.depth  -- nearest team first; the org default has no depth and sorts last
    limit 1
    """
)
_OWNER_TEAM = text("select team_id from memberships where organization_id = :o and user_id = :u")
_PLAN = text(
    "select p.max_quorum, p.max_rounds, p.max_levels from organizations o"
    " join plans p on p.key = o.plan_key where o.id = :o"
)


LEVEL_ROLES = frozenset({"team_lead", "manager", "org_admin"})


def _valid_levels(levels: Any) -> bool:
    return isinstance(levels, list) and all(
        isinstance(lv, dict)
        and lv.get("role") in LEVEL_ROLES
        and type(lv.get("timeout_minutes")) is int
        and lv["timeout_minutes"] >= 1
        for lv in levels
    )


async def resolve_policy(
    session: AsyncSession, organization_id: uuid.UUID, owner_user_id: uuid.UUID
) -> dict[str, Any]:
    """The policy that applies to this owner, as the JSON snapshot stored on the request."""
    row = (
        (await session.execute(_RESOLVE, {"o": organization_id, "u": owner_user_id}))
        .mappings()
        .first()
    )
    if row is None:
        raise PolicyUnresolved  # no team policy and no org default: nothing may decide this request
    if not _valid_levels(
        row["levels"]
    ):  # fail closed now, not later when the first step cannot open
        raise PolicyUnresolved
    owner_team = (
        await session.execute(_OWNER_TEAM, {"o": organization_id, "u": owner_user_id})
    ).scalar()
    return {
        # The requester's own team, frozen with the request: a team_lead step is scoped to it (D16).
        "owner_team_id": str(owner_team) if owner_team else None,
        "policy_id": str(row["id"]),
        "team_id": str(row["team_id"]) if row["team_id"] else None,
        "quorum_size": row["quorum_size"],
        "require_distinct_agents": row["require_distinct_agents"],
        "rounds": row["rounds"],
        "confidence_threshold": row["confidence_threshold"],
        "levels": row["levels"],
        "voting_timeout_minutes": row["voting_timeout_minutes"],
        "risk_floors": row["risk_floors"],
    }


async def ensure_fits_plan(
    session: AsyncSession, organization_id: uuid.UUID, snapshot: dict[str, Any]
) -> None:
    """Fail closed (never clamp): a policy above the plan's caps cannot create requests."""
    row = (await session.execute(_PLAN, {"o": organization_id})).first()
    if row is None:
        raise PolicyExceedsPlan
    max_quorum, max_rounds, max_levels = row  # NULL max_quorum = any
    if (
        (max_quorum is not None and snapshot["quorum_size"] > max_quorum)
        or snapshot["rounds"] > max_rounds
        or len(snapshot["levels"]) > max_levels
    ):
        raise PolicyExceedsPlan

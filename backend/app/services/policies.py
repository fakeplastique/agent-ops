import json
import uuid
from typing import Any

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.errors import (
    AlreadyExists,
    InvalidInput,
    NotFoundError,
    PlanFeatureRequired,
    PolicyAbovePlan,
    PolicyExceedsPlan,
    PolicyUnresolved,
)
from app.domain.risk import ACTION_TYPE
from app.services import audit, features
from app.services.validation import check_text

RISKS = frozenset({"low", "medium", "high"})

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
    "select p.key, p.max_quorum, p.max_rounds, p.max_levels from organizations o"
    " join plans p on p.key = o.plan_key where o.id = :o"
)
_COLS = (
    "id, team_id, name, quorum_size, require_distinct_agents, rounds, confidence_threshold, levels,"
    " voting_timeout_minutes, risk_floors, created_at, updated_at"
)
FIELDS = (
    "name", "quorum_size", "require_distinct_agents", "rounds", "confidence_threshold", "levels",
    "voting_timeout_minutes", "risk_floors",
)  # fmt: skip
_JSON = ("levels", "risk_floors")
_LIST = text(
    f"select {_COLS}, count(*) over () as total from escalation_policies where organization_id = :o"
    " order by team_id nulls first, name, id offset :skip limit :limit"
)
_ONE = text(f"select {_COLS} from escalation_policies where organization_id = :o and id = :i")
_LOCK_ONE = text(
    f"select {_COLS} from escalation_policies where organization_id = :o and id = :i for update"
)
_TEAM = text("select 1 from teams where organization_id = :o and id = :t")
_INSERT = text(
    f"insert into escalation_policies (organization_id, team_id, {', '.join(FIELDS)}) values (:o, :t, "
    + ", ".join(f"cast(:{f} as jsonb)" if f in _JSON else f":{f}" for f in FIELDS)
    + f") returning {_COLS}"
)
_UPDATE = text(
    "update escalation_policies set "
    + ", ".join(f"{f} = cast(:{f} as jsonb)" if f in _JSON else f"{f} = :{f}" for f in FIELDS)
    + f", updated_at = now() where organization_id = :o and id = :i returning {_COLS}"
)
_DELETE = text(
    "delete from escalation_policies where organization_id = :o and id = :i returning id"
)
TEAM_FLAG = "team_policies"


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


async def _over_plan(
    session: AsyncSession, organization_id: uuid.UUID, policy: dict[str, Any]
) -> PolicyAbovePlan | None:
    """The first plan cap this policy breaks, with the limit named; None when it fits."""
    row = (await session.execute(_PLAN, {"o": organization_id})).first()
    if row is None:
        return PolicyAbovePlan()
    plan, max_quorum, max_rounds, max_levels = row  # NULL max_quorum = any
    for limit, cap, value in (
        ("quorum_size", max_quorum, policy["quorum_size"]),
        ("rounds", max_rounds, policy["rounds"]),
        ("levels", max_levels, len(policy["levels"])),
    ):
        if cap is not None and value > cap:
            return PolicyAbovePlan(limit, cap, value, plan)
    return None


async def ensure_fits_plan(
    session: AsyncSession, organization_id: uuid.UUID, snapshot: dict[str, Any]
) -> None:
    """Fail closed (never clamp): a policy above the plan's caps cannot create requests."""
    if await _over_plan(session, organization_id, snapshot) is not None:
        raise PolicyExceedsPlan


def _validate(policy: dict[str, Any]) -> None:
    """Service-side too: MCP and future callers reach here without the REST DTO."""
    check_text("name", policy["name"], 1, 80)
    for key in ("quorum_size", "voting_timeout_minutes"):
        if type(policy[key]) is not int or policy[key] < 1:
            raise InvalidInput(f"{key} must be a positive integer")
    if type(policy["rounds"]) is not int or policy["rounds"] not in (1, 2):
        raise InvalidInput("rounds must be 1 or 2")
    c = policy["confidence_threshold"]
    if type(c) not in (int, float) or not 0 <= c <= 1:
        raise InvalidInput("confidence_threshold must be between 0 and 1")
    if type(policy["require_distinct_agents"]) is not bool:
        raise InvalidInput("require_distinct_agents must be true or false")
    if not policy["levels"] or not _valid_levels(policy["levels"]):
        raise InvalidInput(
            "levels need a role (team_lead, manager, org_admin) and timeout_minutes >= 1"
        )
    floors = policy["risk_floors"]
    if not isinstance(floors, dict) or not all(
        isinstance(k, str) and ACTION_TYPE.fullmatch(k) and v in RISKS for k, v in floors.items()
    ):
        raise InvalidInput("risk_floors maps an action_type slug to low, medium or high")


async def _check(session: AsyncSession, org: uuid.UUID, team: Any, policy: dict[str, Any]) -> None:
    _validate(policy)
    if team is not None and not await features.is_enabled(session, org, TEAM_FLAG):
        raise PlanFeatureRequired(TEAM_FLAG, "enterprise")
    if (over := await _over_plan(session, org, policy)) is not None:
        raise over


def _params(policy: dict[str, Any]) -> dict[str, Any]:
    return {f: json.dumps(policy[f]) if f in _JSON else policy[f] for f in FIELDS}


async def create_policy(
    session: AsyncSession, org: uuid.UUID, actor: uuid.UUID, team: uuid.UUID | None,
    policy: dict[str, Any],
) -> dict[str, Any]:  # fmt: skip
    """The org default (team None) or one team's policy; checked against the plan before it is saved."""
    await _check(session, org, team, policy)
    if team is not None and (await session.execute(_TEAM, {"o": org, "t": team})).first() is None:
        raise NotFoundError("team not found")
    try:
        async with session.begin_nested():
            row = (
                (await session.execute(_INSERT, {"o": org, "t": team} | _params(policy)))
                .mappings()
                .one()
            )
    except IntegrityError as e:  # (organization_id, team_id) unique: one default, one per team
        raise AlreadyExists("this scope already has a policy") from e
    await audit.record(session, org, "escalation_policy", row["id"], "created", "user", actor)
    return dict(row)


async def list_policies(
    session: AsyncSession, org: uuid.UUID, skip: int, limit: int
) -> tuple[list[dict], int]:
    rows = (await session.execute(_LIST, {"o": org, "skip": skip, "limit": limit})).mappings().all()
    return [dict(r) for r in rows], (rows[0]["total"] if rows else 0)


async def get_policy(session: AsyncSession, org: uuid.UUID, policy_id: uuid.UUID) -> dict:
    row = (await session.execute(_ONE, {"o": org, "i": policy_id})).mappings().first()
    if row is None:
        raise NotFoundError
    return dict(row)


async def update_policy(
    session: AsyncSession, org: uuid.UUID, actor: uuid.UUID, policy_id: uuid.UUID,
    fields: dict[str, Any],
) -> dict:  # fmt: skip
    """Merge the sent fields and re-check the whole policy: edits cannot sneak past the plan."""
    # Row lock before the merge: a concurrent PATCH must merge into this one's result, not the old row.
    current = (await session.execute(_LOCK_ONE, {"o": org, "i": policy_id})).mappings().first()
    if current is None:
        raise NotFoundError
    merged = {f: fields.get(f, current[f]) for f in FIELDS}
    # No flag check here: the team was gated at create, and after a downgrade the admin must be able
    # to edit the policy down to the plan (the above-plan check still applies).
    await _check(session, org, None, merged)
    row = (
        (await session.execute(_UPDATE, {"o": org, "i": policy_id} | _params(merged)))
        .mappings()
        .one()
    )
    await audit.record(session, org, "escalation_policy", policy_id, "updated", "user", actor)
    return dict(row)


async def delete_policy(
    session: AsyncSession, org: uuid.UUID, actor: uuid.UUID, policy_id: uuid.UUID
) -> None:
    """Open requests keep their frozen snapshot; only new requests see the change."""
    if (await session.execute(_DELETE, {"o": org, "i": policy_id})).first() is None:
        raise NotFoundError
    await audit.record(session, org, "escalation_policy", policy_id, "deleted", "user", actor)

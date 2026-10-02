from collections.abc import Mapping
from dataclasses import dataclass
from uuid import UUID

from app.authz.matrix import Action, Role, can
from app.authz.principals import Principal, UserPrincipal


@dataclass(frozen=True)
class StepFacts:
    organization_id: UUID
    required_role: Role
    requested_by_user_id: UUID | None  # None when an agent created the request
    agent_owner_id: UUID | None
    request_team_id: UUID | None
    team_parents: Mapping[UUID, UUID | None]
    prior_decider_ids: frozenset[UUID]  # users who decided a step of this request version

    def __post_init__(self) -> None:
        object.__setattr__(self, "required_role", Role(self.required_role))


def leads_team_or_ancestor(
    team_id: UUID | None, led: frozenset[UUID], parents: Mapping[UUID, UUID | None]
) -> bool:
    seen: set[UUID] = set()  # a cyclic parent chain must end, not hang
    while team_id is not None and team_id not in seen:
        if team_id in led:
            return True
        seen.add(team_id)
        team_id = parents.get(team_id)
    return False


def may_decide(who: Principal, step: StepFacts, led_team_ids: frozenset[UUID]) -> bool:
    if not isinstance(who, UserPrincipal):
        return False
    if who.organization_id != step.organization_id:  # do not rely on RLS alone
        return False
    if who.user_id in (step.requested_by_user_id, step.agent_owner_id):
        return False
    if who.user_id in step.prior_decider_ids:
        return False
    # Member steps do not exist: deny. Otherwise the role must reach the step's level.
    if step.required_role is Role.MEMBER or who.role < step.required_role:
        return False
    if not can(who.role, Action.DECIDE_STEP):
        return False
    if who.role is Role.TEAM_LEAD:  # manager and org_admin are org-wide
        return leads_team_or_ancestor(step.request_team_id, led_team_ids, step.team_parents)
    return True

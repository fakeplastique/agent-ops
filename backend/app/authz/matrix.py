from enum import IntEnum, StrEnum


class Role(IntEnum):
    # Order is the permission order: member < team_lead < manager < org_admin.
    MEMBER = 1
    TEAM_LEAD = 2
    MANAGER = 3
    ORG_ADMIN = 4


class Action(StrEnum):
    READ = "read"
    CREATE_REQUEST = "create_request"
    POST_MESSAGE = "post_message"
    DECIDE_STEP = "decide_step"  # the step's required_role and team scope are checked in duties.py
    MANAGE_ORG = "manage_org"
    READ_PLAN = "read_plan"
    # Role only; the DEMO_MODE switch is checked where the plan changes.
    CHANGE_PLAN = "change_plan"


# The lowest role allowed per action (spec section 2). A missing action is a KeyError, never a grant.
MIN_ROLE: dict[Action, Role] = {
    Action.READ: Role.MEMBER,
    Action.CREATE_REQUEST: Role.MEMBER,
    Action.POST_MESSAGE: Role.MEMBER,
    Action.DECIDE_STEP: Role.TEAM_LEAD,
    Action.MANAGE_ORG: Role.ORG_ADMIN,
    Action.READ_PLAN: Role.MEMBER,
    Action.CHANGE_PLAN: Role.ORG_ADMIN,
}


def can(role: Role, action: Action) -> bool:
    return role >= MIN_ROLE[action]

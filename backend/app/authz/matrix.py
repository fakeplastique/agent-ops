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
    DECIDE_TEAM_LEAD_STEP = "decide_team_lead_step"
    DECIDE_MANAGER_STEP = "decide_manager_step"
    DECIDE_ORG_ADMIN_STEP = "decide_org_admin_step"
    MANAGE_ORG = "manage_org"
    READ_PLAN = "read_plan"
    # Role only; the DEMO_MODE switch is checked where the plan changes.
    CHANGE_PLAN = "change_plan"


# The lowest role allowed per action (spec section 2). A missing action is a KeyError, never a grant.
MIN_ROLE: dict[Action, Role] = {
    Action.READ: Role.MEMBER,
    Action.CREATE_REQUEST: Role.MEMBER,
    Action.POST_MESSAGE: Role.MEMBER,
    Action.DECIDE_TEAM_LEAD_STEP: Role.TEAM_LEAD,
    Action.DECIDE_MANAGER_STEP: Role.MANAGER,
    Action.DECIDE_ORG_ADMIN_STEP: Role.ORG_ADMIN,
    Action.MANAGE_ORG: Role.ORG_ADMIN,
    Action.READ_PLAN: Role.MEMBER,
    Action.CHANGE_PLAN: Role.ORG_ADMIN,
}

# A step's required_role picks which decide action it needs.
STEP_ACTION: dict[Role, Action] = {
    Role.TEAM_LEAD: Action.DECIDE_TEAM_LEAD_STEP,
    Role.MANAGER: Action.DECIDE_MANAGER_STEP,
    Role.ORG_ADMIN: Action.DECIDE_ORG_ADMIN_STEP,
}


def can(role: Role, action: Action) -> bool:
    return role >= MIN_ROLE[action]

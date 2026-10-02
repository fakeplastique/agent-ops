from app.authz.matrix import STEP_ACTION, Action, Role, can

# Literal copy of the spec section 2 table, in member/team_lead/manager/org_admin order.
SPEC = {
    Action.READ: (1, 1, 1, 1),
    Action.CREATE_REQUEST: (1, 1, 1, 1),
    Action.POST_MESSAGE: (1, 1, 1, 1),
    Action.DECIDE_TEAM_LEAD_STEP: (0, 1, 1, 1),
    Action.DECIDE_MANAGER_STEP: (0, 0, 1, 1),
    Action.DECIDE_ORG_ADMIN_STEP: (0, 0, 0, 1),
    Action.MANAGE_ORG: (0, 0, 0, 1),
    Action.READ_PLAN: (1, 1, 1, 1),
    Action.CHANGE_PLAN: (0, 0, 0, 1),
}
ROLES = (Role.MEMBER, Role.TEAM_LEAD, Role.MANAGER, Role.ORG_ADMIN)


def test_matrix_equals_spec_table():
    for action, row in SPEC.items():
        for role, allowed in zip(ROLES, row, strict=True):
            assert can(role, action) is bool(allowed), (role, action)


def test_every_action_is_in_the_matrix():
    # A new Action without a spec row (and a role) fails here, not in production.
    assert set(Action) == set(SPEC)


def test_role_order_is_member_to_org_admin():
    assert list(ROLES) == sorted(ROLES)


def test_every_step_level_has_a_decide_action():
    assert set(STEP_ACTION) == {Role.TEAM_LEAD, Role.MANAGER, Role.ORG_ADMIN}

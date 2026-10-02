import signal
from contextlib import contextmanager
from dataclasses import replace
from uuid import uuid4

import pytest

from app.authz.duties import StepFacts, may_decide
from app.authz.matrix import Role
from app.authz.principals import AgentPrincipal, UserPrincipal

ORG = uuid4()
ROOT, MID, LEAF, SIBLING = (uuid4() for _ in range(4))
# ROOT > MID > LEAF; SIBLING is a child of ROOT beside MID.
PARENTS = {ROOT: None, MID: ROOT, LEAF: MID, SIBLING: ROOT}


def user(role: Role) -> UserPrincipal:
    return UserPrincipal(uuid4(), ORG, role)


@contextmanager
def within(seconds: int):
    # No pytest-timeout: a hang must fail this test, not stall CI.
    def boom(*_):
        raise TimeoutError("did not terminate")

    old = signal.signal(signal.SIGALRM, boom)
    signal.alarm(seconds)
    try:
        yield
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, old)


def step(required_role=Role.MANAGER, **changes) -> StepFacts:
    base = StepFacts(
        organization_id=ORG,
        required_role=required_role,
        requested_by_user_id=None,
        agent_owner_id=None,
        request_team_id=LEAF,
        team_parents=PARENTS,
        prior_decider_ids=frozenset(),
    )
    return replace(base, **changes)


def test_agent_principal_never_decides_human_step():
    agent = AgentPrincipal(uuid4(), ORG)
    for role in (Role.TEAM_LEAD, Role.MANAGER, Role.ORG_ADMIN):
        assert not may_decide(agent, step(role), frozenset({LEAF}))


def test_owner_of_agent_cannot_decide():
    owner = user(Role.ORG_ADMIN)
    assert not may_decide(owner, step(agent_owner_id=owner.user_id), frozenset())
    assert may_decide(owner, step(), frozenset())  # same admin, not the owner: allowed


def test_creator_cannot_decide_own_request():
    creator = user(Role.ORG_ADMIN)
    assert not may_decide(creator, step(requested_by_user_id=creator.user_id), frozenset())


def test_team_lead_of_sibling_team_denied_ancestor_lead_allowed():
    s = step(Role.TEAM_LEAD)  # request belongs to LEAF
    lead = user(Role.TEAM_LEAD)
    assert not may_decide(lead, s, frozenset({SIBLING}))
    assert may_decide(lead, s, frozenset({LEAF}))
    assert may_decide(lead, s, frozenset({MID}))
    assert may_decide(lead, s, frozenset({ROOT}))


def test_team_lead_denied_when_request_has_no_team():
    assert not may_decide(
        user(Role.TEAM_LEAD), step(Role.TEAM_LEAD, request_team_id=None), frozenset({ROOT})
    )


def test_team_chain_cycle_terminates():
    a, b = uuid4(), uuid4()
    s = step(Role.TEAM_LEAD, request_team_id=a, team_parents={a: b, b: a})
    with within(2):
        assert not may_decide(user(Role.TEAM_LEAD), s, frozenset({SIBLING}))


def test_user_with_prior_step_on_this_version_denied():
    # Which deciders count for a version is the caller's query (S10 pins the new-version case).
    admin = user(Role.ORG_ADMIN)
    assert not may_decide(admin, step(prior_decider_ids=frozenset({admin.user_id})), frozenset())


def test_principal_of_another_organization_denied():
    other = UserPrincipal(uuid4(), uuid4(), Role.ORG_ADMIN)
    assert not may_decide(other, step(), frozenset())
    assert may_decide(user(Role.ORG_ADMIN), step(), frozenset())


def test_role_below_required_denied_and_org_wide_roles_need_no_team():
    assert not may_decide(user(Role.MEMBER), step(Role.TEAM_LEAD), frozenset({LEAF}))
    assert not may_decide(user(Role.TEAM_LEAD), step(Role.MANAGER), frozenset({LEAF}))
    assert not may_decide(user(Role.MANAGER), step(Role.ORG_ADMIN), frozenset())
    assert may_decide(user(Role.MANAGER), step(Role.TEAM_LEAD), frozenset())
    assert may_decide(user(Role.ORG_ADMIN), step(Role.ORG_ADMIN, request_team_id=None), frozenset())


def test_step_with_no_decide_action_is_denied_not_a_crash():
    assert not may_decide(user(Role.ORG_ADMIN), step(Role.MEMBER), frozenset())


def test_raw_int_role_still_gets_team_scope_and_member_steps_deny():
    # An `is` check let a raw int role skip the team-scope branch (r2 F1/F2).
    not_leader = UserPrincipal(uuid4(), ORG, 2)  # type: ignore[arg-type]
    assert not may_decide(not_leader, step(Role.TEAM_LEAD), frozenset())
    assert not may_decide(user(Role.ORG_ADMIN), step(1), frozenset())  # type: ignore[arg-type]


def test_out_of_range_role_is_rejected():
    with pytest.raises(ValueError):
        UserPrincipal(uuid4(), ORG, 9)  # type: ignore[arg-type]

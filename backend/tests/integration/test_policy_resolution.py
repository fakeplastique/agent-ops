from itertools import pairwise

import pytest
from sqlalchemy import text

from app.db.session import tenant_session
from app.domain.errors import PolicyUnresolved
from app.services.policies import resolve_policy

from .helpers import make_member, make_org, make_policy, make_team, make_user

pytestmark = pytest.mark.anyio


async def resolve(app_engine, org, user):
    async with tenant_session(app_engine, org) as s:
        # A missing cycle guard would recurse forever: fail the test in seconds instead of hanging CI.
        await s.execute(text("set local statement_timeout = '3s'"))
        return await resolve_policy(s, org, user)


async def _chain(owner, org, depth=3):
    """leaf -> ... -> root teams; returns [leaf, ..., root]."""
    teams = [await make_team(owner, org)]
    for _ in range(depth - 1):
        teams.append(await make_team(owner, org))
    for child, parent in pairwise(teams):
        async with owner.begin() as c:
            await c.execute(
                text("update teams set parent_team_id=:p where id=:c"), {"p": parent, "c": child}
            )
    return teams


async def test_agent_owner_team_then_ancestor_then_org_default(owner_engine, app_engine):
    org = await make_org(owner_engine, "pro")
    leaf, mid, root = await _chain(owner_engine, org)
    user = await make_user(owner_engine)
    await make_member(owner_engine, org, user, team=leaf)
    default = await make_policy(owner_engine, org, quorum=3)
    assert (await resolve(app_engine, org, user))["policy_id"] == str(default)  # no team policy yet
    root_p = await make_policy(owner_engine, org, root, quorum=5)
    assert (await resolve(app_engine, org, user))["policy_id"] == str(
        root_p
    )  # skips teams without a policy
    mid_p = await make_policy(owner_engine, org, mid, quorum=4)
    snap = await resolve(app_engine, org, user)
    assert snap["policy_id"] == str(mid_p) and snap["quorum_size"] == 4  # nearest ancestor wins
    leaf_p = await make_policy(owner_engine, org, leaf, quorum=2)
    assert (await resolve(app_engine, org, user))["policy_id"] == str(
        leaf_p
    )  # catches: stopping at the first team without policy


async def test_owner_without_team_gets_org_default(owner_engine, app_engine):
    org = await make_org(owner_engine, "pro")
    team = await make_team(owner_engine, org)
    await make_policy(owner_engine, org, team, quorum=7)
    default = await make_policy(owner_engine, org)
    user = await make_user(owner_engine)
    await make_member(owner_engine, org, user)  # no team
    assert (await resolve(app_engine, org, user))["policy_id"] == str(default)
    stranger = await make_user(owner_engine)  # not even a member
    assert (await resolve(app_engine, org, stranger))["policy_id"] == str(default)


async def test_no_policy_at_all_fails_closed(owner_engine, app_engine):
    org = await make_org(owner_engine)
    user = await make_user(owner_engine)
    with pytest.raises(PolicyUnresolved):
        await resolve(app_engine, org, user)


async def test_cyclic_team_chain_terminates(owner_engine, app_engine):
    org = await make_org(owner_engine)
    a, b = await _chain(owner_engine, org, 2)
    async with owner_engine.begin() as c:
        await c.execute(text("update teams set parent_team_id=:b where id=:a"), {"a": a, "b": b})
        await c.execute(text("update teams set parent_team_id=:a where id=:b"), {"a": a, "b": b})
    user = await make_user(owner_engine)
    await make_member(owner_engine, org, user, team=a)
    default = await make_policy(owner_engine, org)
    assert (await resolve(app_engine, org, user))["policy_id"] == str(default)


async def test_another_orgs_team_policy_is_never_used(owner_engine, app_engine):
    a, b = await make_org(owner_engine), await make_org(owner_engine)
    team_b = await make_team(owner_engine, b)
    await make_policy(owner_engine, b, team_b, quorum=7)
    default_a = await make_policy(owner_engine, a)
    user = await make_user(owner_engine)
    await make_member(owner_engine, b, user, team=team_b)  # same person, a member of B's team
    assert (await resolve(app_engine, a, user))["policy_id"] == str(
        default_a
    )  # catches: resolution crossing tenants


async def test_snapshot_holds_the_resolved_values(owner_engine, app_engine):
    org = await make_org(owner_engine)
    await make_policy(
        owner_engine,
        org,
        quorum=2,
        rounds=1,
        threshold=0.9,
        timeout=45,
        levels=[{"role": "team_lead", "timeout_minutes": 10}],
        floors={"refund": "high"},
    )
    snap = await resolve(app_engine, org, await make_user(owner_engine))
    assert snap | {"policy_id": None, "team_id": None} == {
        "policy_id": None,
        "team_id": None,
        "owner_team_id": None,
        "quorum_size": 2,
        "require_distinct_agents": True,
        "rounds": 1,
        "confidence_threshold": 0.9,
        "levels": [{"role": "team_lead", "timeout_minutes": 10}],
        "voting_timeout_minutes": 45,
        "risk_floors": {"refund": "high"},
    }

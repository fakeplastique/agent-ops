import json

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from tests.integration.helpers import (
    make_agent,
    make_org,
    make_policy,
    make_team,
    make_user,
    make_world,
)

pytestmark = pytest.mark.anyio

TENANT = {
    "teams",
    "memberships",
    "agent_identities",
    "agent_sessions",
    "escalation_policies",
    "decision_requests",
}
NEW = TENANT | {"users"}


async def refused(owner, sql, **p) -> bool:
    try:
        async with owner.begin() as c:
            await c.execute(text(sql), p)
    except DBAPIError:
        return True
    return False


async def test_db_rejects_bad_numbers_and_enums(owner_engine):
    w = await make_world(owner_engine)
    team = await make_team(owner_engine, w.org)  # make_world already holds the org default policy
    pol = "insert into escalation_policies (organization_id, team_id, name, quorum_size, rounds, confidence_threshold, levels, voting_timeout_minutes) values (:o, :tm, :n, :q, :r, :c, cast(:l as jsonb), :t)"
    ok = {
        "o": w.org,
        "tm": team,
        "n": "x",
        "q": 3,
        "r": 1,
        "c": 0.8,
        "l": json.dumps([{"role": "manager", "timeout_minutes": 5}]),
        "t": 60,
    }
    for change in (
        {"q": 0},
        {"r": 3},
        {"c": 1.5},
        {"c": -0.1},
        {"t": 0},
        {"l": "[]"},
        {"l": "{}"},
    ):  # catches: validation only in Python
        assert await refused(owner_engine, pol, **ok | change), change
    assert not await refused(owner_engine, pol, **ok)
    bad_member = "insert into memberships (organization_id, user_id, role) values (:o, :u, :r)"
    assert await refused(
        owner_engine, bad_member, o=w.org, u=await make_user(owner_engine), r="root"
    )
    assert await refused(
        owner_engine, "update agent_identities set status='gone' where id=:i", i=w.identity
    )


async def test_decision_request_constraints(owner_engine):
    w = await make_world(owner_engine)
    sql = (
        "insert into decision_requests (organization_id, title, description, action_type, payload, risk_level,"
        " effective_risk_level, status, version, content_hash, requested_by_session_id, requested_by_user_id,"
        " policy_snapshot, voting_deadline_at) values (:o, :t, '', 'a', cast(:p as jsonb), :r, 'low', :s, :v, :h,"
        " :sess, :usr, '{}', now())"
    )
    ok = {
        "o": w.org,
        "t": "T",
        "p": "{}",
        "r": "low",
        "s": "pending_votes",
        "v": 1,
        "h": "a" * 64,
        "sess": w.session,
        "usr": None,
    }
    assert not await refused(owner_engine, sql, **ok)
    for change in (
        {"t": ""},
        {"t": "x" * 201},
        {"r": "critical"},
        {"s": "done"},
        {"v": 0},
        {"h": "A" * 64},
        {"h": "short"},
        {"p": json.dumps({"k": "x" * 70_000})},
        {"sess": None},  # no requester
        {"usr": w.owner_user},  # two requesters
    ):
        assert await refused(owner_engine, sql, **ok | change), change


async def test_unique_agent_name_per_org_but_not_across_orgs(owner_engine):
    a, b = await make_org(owner_engine), await make_org(owner_engine)
    user = await make_user(owner_engine)
    sql = (
        "insert into agent_identities (organization_id, name, owner_user_id) values (:o, 'bot', :u)"
    )
    assert not await refused(owner_engine, sql, o=a, u=user)
    assert await refused(owner_engine, sql, o=a, u=user)
    assert not await refused(
        owner_engine, sql, o=b, u=user
    )  # catches: a global unique leaking existence


async def test_cross_org_references_are_rejected(owner_engine):
    a, b = await make_org(owner_engine), await make_org(owner_engine)
    team_b = await make_team(owner_engine, b)
    user = await make_user(owner_engine)
    assert await refused(
        owner_engine,
        "insert into teams (organization_id, name, parent_team_id) values (:o, 'c', :p)",
        o=a,
        p=team_b,
    )
    assert await refused(
        owner_engine,
        "insert into memberships (organization_id, user_id, team_id, role) values (:o, :u, :t, 'member')",
        o=a,
        u=user,
        t=team_b,
    )
    assert await refused(
        owner_engine,
        "insert into escalation_policies (organization_id, team_id, name, levels)"
        " values (:o, :t, 'p', cast(:l as jsonb))",
        o=a,
        t=team_b,
        l='[{"role": "manager", "timeout_minutes": 1}]',
    )
    ident, _ = await make_agent(owner_engine, b, user)
    assert await refused(
        owner_engine,
        "insert into agent_sessions (organization_id, agent_identity_id, session_label) values (:o, :i, 'x')",
        o=a,
        i=ident,
    )


async def test_one_default_policy_and_one_policy_per_team(owner_engine):
    org = await make_org(owner_engine)
    team = await make_team(owner_engine, org)
    await make_policy(owner_engine, org)
    await make_policy(owner_engine, org, team)
    with pytest.raises(DBAPIError):
        await make_policy(owner_engine, org)  # a second org default would make resolution ambiguous
    with pytest.raises(DBAPIError):
        await make_policy(owner_engine, org, team)


async def test_rls_and_fk_indexes_present_on_new_tables(owner_engine):
    async with owner_engine.connect() as c:
        flags = {
            r[0]: (r[1], r[2])
            for r in await c.execute(
                text(
                    "select relname, relrowsecurity, relforcerowsecurity from pg_class where relkind='r' and relnamespace='public'::regnamespace"
                )
            )
        }
        for t in NEW:
            assert flags[t] == (True, True), t  # catches: a new table without RLS
        fks = (
            await c.execute(
                text(
                    "select conrelid::regclass::text, conname, conkey from pg_constraint where contype='f' and conrelid::regclass::text = any(:t)"
                ),
                {"t": sorted(NEW)},
            )
        ).all()
        for table, name, cols in fks:
            idx = (
                (
                    await c.execute(
                        text(
                            "select indkey::int2[] from pg_index where indrelid = cast(:t as regclass)"
                        ),
                        {"t": table},
                    )
                )
                .scalars()
                .all()
            )
            assert any(list(i)[: len(cols)] == list(cols) for i in idx), (
                f"{table}.{name} has no index"
            )  # catches: a foreign key without an index

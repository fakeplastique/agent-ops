import asyncio
from datetime import timedelta

import pytest
from sqlalchemy import text

from app.db.session import tenant_session
from app.domain.errors import InvalidInput, NotFoundError, PlanLimitReached, PolicyExceedsPlan
from app.domain.hashing import content_hash
from app.domain.statuses import Risk
from app.services.decisions import create_decision

from .helpers import FakeClock, make_member, make_user, make_world, set_usage, utc

pytestmark = pytest.mark.anyio

NOW = FakeClock(utc(2026, 1, 15, 10, 0))
ARGS = {
    "title": "Refund order 7",
    "description": "d",
    "action_type": "refund",
    "payload": {"amount": 5},
    "risk_level": "low",
}


def _deep(n: int):
    out: list = []
    for _ in range(n):
        out = [out]
    return out


async def create(engine, w, clock=NOW, **over):
    kw = {"agent_session_id": w.session} | ARGS | over
    async with tenant_session(engine, w.org) as s:
        return await create_decision(s, w.org, clock, **kw)


async def rows(owner, sql, **p):
    async with owner.connect() as c:
        return (await c.execute(text(sql), p)).all()


async def test_over_limit_returns_402_and_leaves_no_row_and_no_counter_burn(
    app_engine, owner_engine
):
    w = await make_world(owner_engine, "free")
    await set_usage(owner_engine, w.org, "2026-01", 50)
    with pytest.raises(PlanLimitReached):
        await create(app_engine, w)
    assert (
        await rows(
            owner_engine, "select 1 from decision_requests where organization_id=:o", o=w.org
        )
        == []
    )
    assert (
        await rows(
            owner_engine, "select value from usage_counters where organization_id=:o", o=w.org
        )
    )[0][0] == 50


@pytest.mark.parametrize(
    ("plan", "policy"),
    [
        ("free", {"quorum": 5}),
        ("free", {"rounds": 2}),
        ("free", {"levels": [{"role": "manager", "timeout_minutes": 5}] * 2}),
        ("pro", {"quorum": 8}),
    ],
)
async def test_policy_above_plan_is_402_not_clamped_and_burns_nothing(
    app_engine, owner_engine, plan, policy
):
    w = await make_world(owner_engine, plan, **policy)
    with pytest.raises(PolicyExceedsPlan):  # catches: a silent clamp turning 5 reviewers into 3
        await create(app_engine, w)
    assert (
        await rows(
            owner_engine, "select 1 from decision_requests where organization_id=:o", o=w.org
        )
        == []
    )
    # catches: usage consumed before a later failure
    assert (
        await rows(owner_engine, "select 1 from usage_counters where organization_id=:o", o=w.org)
        == []
    )


async def test_plan_limits_are_inclusive_and_enterprise_is_unbounded(app_engine, owner_engine):
    pro = await make_world(owner_engine, "pro", quorum=7, rounds=2)
    assert (await create(app_engine, pro))["status"] == "pending_votes"
    ent = await make_world(owner_engine, "enterprise", quorum=50, rounds=2)
    assert (await create(app_engine, ent))["status"] == "pending_votes"


async def test_create_writes_exactly_one_audit_and_one_outbox_row(app_engine, owner_engine):
    w = await make_world(owner_engine)
    d = await create(app_engine, w)
    audit = await rows(
        owner_engine,
        "select entity_type, entity_id, action, actor_type, actor_id, data from audit_logs where organization_id=:o",
        o=w.org,
    )
    outbox = await rows(
        owner_engine,
        "select type, seq, payload from outbox_events where organization_id=:o",
        o=w.org,
    )
    assert (
        len(audit) == 1 and len(outbox) == 1
    )  # catches: events outside the transaction or doubled
    assert audit[0][:5] == ("decision_request", d["id"], "created", "agent", w.identity)
    assert outbox[0][0] == "decision.created" and outbox[0][1] == 1
    assert (
        "payload" not in audit[0][5] and "payload" not in outbox[0][2]
    )  # payloads never copied into logs


async def test_unknown_action_type_effective_risk_medium(app_engine, owner_engine):
    w = await make_world(owner_engine, floors={"refund": "low"})
    assert (await create(app_engine, w, action_type="brand_new"))[
        "effective_risk_level"
    ] == "medium"  # catches: new types defaulting to low
    assert (await create(app_engine, w))["effective_risk_level"] == "low"  # whitelisted
    assert (await create(app_engine, w, risk_level="high"))[
        "effective_risk_level"
    ] == "high"  # floor never lowers declared


async def test_hash_uses_declared_risk_and_stored_fields(app_engine, owner_engine):
    w = await make_world(owner_engine, floors={"refund": "high"})
    d = await create(app_engine, w, risk_level="low")
    assert d["effective_risk_level"] == "high" and d["risk_level"] == "low"
    assert d["content_hash"] == content_hash(ARGS["title"], "refund", ARGS["payload"], Risk.LOW)
    assert d["status"] == "pending_votes" and d["version"] == 1 and d["receipt"] is None


async def test_deadline_comes_from_the_policy_timeout(app_engine, owner_engine):
    w = await make_world(owner_engine, timeout=30)
    d = await create(app_engine, w)
    assert d["voting_deadline_at"] == NOW.now() + timedelta(minutes=30)


async def test_snapshot_survives_a_later_policy_edit(app_engine, owner_engine):
    w = await make_world(owner_engine, quorum=3)
    d = await create(app_engine, w)
    async with owner_engine.begin() as c:
        await c.execute(
            text("update escalation_policies set quorum_size=1 where id=:p"), {"p": w.policy}
        )
    row = (
        await rows(
            owner_engine, "select policy_snapshot from decision_requests where id=:i", i=d["id"]
        )
    )[0][0]
    assert row["quorum_size"] == 3  # catches: a live policy read at decision time


async def test_user_requester_resolves_by_their_own_team_and_is_recorded(app_engine, owner_engine):
    w = await make_world(owner_engine)
    user = await make_user(owner_engine)
    await make_member(owner_engine, w.org, user)
    async with tenant_session(app_engine, w.org) as s:
        d = await create_decision(s, w.org, NOW, user_id=user, **ARGS)
    assert d["requested_by_user_id"] == user and d["requested_by_session_id"] is None
    audit = await rows(
        owner_engine, "select actor_type, actor_id from audit_logs where entity_id=:i", i=d["id"]
    )
    assert audit == [("user", user)]


async def test_exactly_one_requester(app_engine, owner_engine):
    w = await make_world(owner_engine)
    for who in ({}, {"agent_session_id": w.session, "user_id": w.owner_user}):
        async with tenant_session(app_engine, w.org) as s:
            with pytest.raises(InvalidInput):
                await create_decision(s, w.org, NOW, **who, **ARGS)


async def test_another_tenants_session_is_not_found_and_consumes_nothing(app_engine, owner_engine):
    a, b = await make_world(owner_engine), await make_world(owner_engine)
    with pytest.raises(NotFoundError):  # catches: using tenant A's agent session from tenant B
        async with tenant_session(app_engine, b.org) as s:
            await create_decision(s, b.org, NOW, agent_session_id=a.session, **ARGS)
    assert (
        await rows(
            owner_engine,
            "select 1 from usage_counters where organization_id in (:a, :b)",
            a=a.org,
            b=b.org,
        )
        == []
    )


async def test_revoked_agent_cannot_create(app_engine, owner_engine):
    w = await make_world(owner_engine)
    async with owner_engine.begin() as c:
        await c.execute(
            text("update agent_identities set status='revoked' where id=:i"), {"i": w.identity}
        )
    with pytest.raises(NotFoundError):
        await create(app_engine, w)


@pytest.mark.parametrize(
    "bad",
    [
        {"title": ""},
        {"title": "x" * 201},
        {"title": 5},
        {"description": "x" * 4097},
        {"payload": {"k": "x" * 70_000}},
        {"payload": ["not", "an", "object"]},
        {"action_type": "Refund "},
        {"risk_level": "critical"},
        {"risk_level": True},
        {"title": "a\x00b"},  # Postgres text cannot hold NUL: would be a 500
        {"payload": {"k": "a\x00b"}},
        {"payload": {"a\x00": 1}},
        {"payload": {"n": float("nan")}},
        {"description": "\ud800"},  # lone surrogate: asyncpg would raise a 500
        {
            "payload": {"deep": _deep(5000)}
        },  # past the recursion limit: InvalidInput, not RecursionError
        # 59 KB canonical, 71 KB as stored jsonb text: the app and the CHECK must agree.
        {"payload": {f"k{i}": 0 for i in range(6000)}},
    ],
)
async def test_invalid_input_is_rejected_before_anything_is_written(app_engine, owner_engine, bad):
    w = await make_world(owner_engine)
    with pytest.raises(InvalidInput):
        await create(app_engine, w, **bad)
    assert (
        await rows(owner_engine, "select 1 from usage_counters where organization_id=:o", o=w.org)
        == []
    )


async def test_two_creates_in_parallel_both_count(app_engine, owner_engine):
    w = await make_world(owner_engine)
    await asyncio.gather(create(app_engine, w), create(app_engine, w))
    assert (
        await rows(
            owner_engine, "select value from usage_counters where organization_id=:o", o=w.org
        )
    )[0][0] == 2
    assert sorted(
        r[0]
        for r in await rows(
            owner_engine, "select seq from outbox_events where organization_id=:o", o=w.org
        )
    ) == [1, 2]


async def test_a_user_who_is_not_a_member_cannot_create(app_engine, owner_engine):
    w = await make_world(owner_engine)
    outsider = await make_user(owner_engine)
    with pytest.raises(NotFoundError):  # catches: a bare user id standing in for a principal
        async with tenant_session(app_engine, w.org) as s:
            await create_decision(s, w.org, NOW, user_id=outsider, **ARGS)
    assert (
        await rows(owner_engine, "select 1 from usage_counters where organization_id=:o", o=w.org)
        == []
    )

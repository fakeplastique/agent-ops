from datetime import timedelta

import pytest
from sqlalchemy import text

from app.db.session import tenant_session
from app.services.votes import visible_votes

from .helpers import (
    NOW,
    FakeClock,
    approval_world,
    cast,
    decide,
    fetch,
    make_world,
    open_request,
    pending_step,
    post,
    staff,
    timeouts,
    voters,
)  # fmt: skip

pytestmark = [pytest.mark.anyio, pytest.mark.usefixtures("drained")]

LOW = {"refund": "low"}
ONE = [{"role": "manager", "timeout_minutes": 60}]


async def req_row(owner, req):
    return (
        await fetch(
            owner,
            "select status, escalation_reason, receipt, voting_deadline_at, version from decision_requests where id=:r",
            r=req["id"],
        )
    )[0]


async def step_due(owner, step):
    return (await fetch(owner, "select due_at from approval_steps where id=:s", s=step))[0][0]


async def test_no_auto_approve_voting_deadline_with_two_of_three_approve_low_risk(
    app_engine, owner_engine, worker_engine
):
    w = await make_world(owner_engine, floors=LOW)
    req = await open_request(app_engine, w)
    a, b, _ = await voters(owner_engine, w, 3)
    await cast(app_engine, w, req, a)
    await cast(app_engine, w, req, b)
    out = await timeouts(app_engine, worker_engine, req["voting_deadline_at"])
    status, reason, receipt, *_ = await req_row(owner_engine, req)
    # catches: a partial quorum approving on timeout
    assert (status, reason, receipt) == ("pending_approval", "quorum_not_reached", None)
    assert out["requests"] == 1
    _step, level, _ = await pending_step(owner_engine, req)
    assert level == 1


async def test_quorum_not_reached_goes_to_humans_with_reason_and_nothing_else_happens_early(
    app_engine, owner_engine, worker_engine
):
    w = await make_world(owner_engine)
    req = await open_request(app_engine, w)  # nobody votes
    just_before = req["voting_deadline_at"] - timedelta(seconds=1)
    assert (await timeouts(app_engine, worker_engine, just_before)) == {
        "steps": 0,
        "requests": 0,
        "errors": 0,
    }
    assert (await req_row(owner_engine, req))[0] == "pending_votes"
    await timeouts(app_engine, worker_engine, req["voting_deadline_at"])  # catches: silent expiry
    status, reason, *_ = await req_row(owner_engine, req)
    assert (status, reason) == ("pending_approval", "quorum_not_reached")
    events = await fetch(
        owner_engine,
        "select type from outbox_events where organization_id=:o order by seq",
        o=w.org,
    )
    kinds = [e[0] for e in events]
    assert kinds[-3:] == ["round.revealed", "decision.status_changed", "step.created"]


async def test_the_deadline_reveals_round_one_once_and_humans_can_read_the_votes(
    app_engine, owner_engine, worker_engine
):
    w = await make_world(owner_engine)
    req = await open_request(app_engine, w)
    a, b, _ = await voters(owner_engine, w, 3)
    await cast(app_engine, w, req, a, "reject", rationale="no way")
    await cast(app_engine, w, req, b, "approve")
    async with tenant_session(app_engine, w.org) as s:
        assert (await visible_votes(s, w.org, req["id"], viewer_session_id=None))["votes"] == []
    await timeouts(app_engine, worker_engine, req["voting_deadline_at"])
    await timeouts(app_engine, worker_engine, req["voting_deadline_at"])
    async with tenant_session(app_engine, w.org) as s:
        seen = await visible_votes(s, w.org, req["id"], viewer_session_id=None)
    assert sorted(v["verdict"] for v in seen["votes"]) == [
        "approve",
        "reject",
    ]  # no more votes can arrive: reveal
    assert (
        len(
            await fetch(
                owner_engine,
                "select 1 from outbox_events where type='round.revealed' and organization_id=:o",
                o=w.org,
            )
        )
        == 1
    )


async def test_a_discussion_nobody_joined_escalates_at_its_deadline(
    app_engine, owner_engine, worker_engine
):
    w = await make_world(owner_engine, "pro", rounds=2, timeout=30)
    req = await open_request(app_engine, w)
    for s, v in zip(
        await voters(owner_engine, w, 3), ("approve", "approve", "reject"), strict=True
    ):
        await cast(app_engine, w, req, s, v)
    deadline = (await req_row(owner_engine, req))[3]
    assert deadline == NOW.now() + timedelta(
        minutes=30
    )  # the timer restarted on entering the discussion
    await timeouts(
        app_engine, worker_engine, NOW.now() + timedelta(minutes=29)
    )  # round 1's old deadline has passed
    assert (await req_row(owner_engine, req))[0] == "pending_discussion"
    await timeouts(app_engine, worker_engine, deadline)
    status, reason, *_ = await req_row(owner_engine, req)
    assert (status, reason) == ("pending_approval", "quorum_not_reached")


async def test_last_level_timeout_expires(app_engine, owner_engine, worker_engine):
    _w, req, _ = await approval_world(owner_engine, app_engine, levels=ONE)
    step, *_ = await pending_step(owner_engine, req)
    await timeouts(app_engine, worker_engine, await step_due(owner_engine, step))
    status, _, receipt, *_ = await req_row(owner_engine, req)
    assert (status, receipt) == ("expired", None)  # catches: a stand-in chain that never ends
    assert (await fetch(owner_engine, "select status from approval_steps where id=:s", s=step))[0][
        0
    ] == "timed_out"
    assert (
        await fetch(
            owner_engine,
            "select 1 from approval_steps where decision_request_id=:r and status='pending'",
            r=req["id"],
        )
        == []
    )


async def test_the_whole_ladder_stand_in_then_stand_in_then_expiry(
    app_engine, owner_engine, worker_engine
):
    _w, req, _ = await approval_world(owner_engine, app_engine, high=True)
    levels_seen = []
    for expected in (1, 2, 3):
        step, level, _ = await pending_step(owner_engine, req)
        assert level == expected
        levels_seen.append(level)
        due = await step_due(owner_engine, step)
        await timeouts(app_engine, worker_engine, due)
        if expected < 3:
            nxt, nlevel, _role = await pending_step(owner_engine, req)
            stands = (
                await fetch(
                    owner_engine,
                    "select stands_in_for_level, due_at from approval_steps where id=:s",
                    s=nxt,
                )
            )[0]
            assert (
                nlevel == expected + 1
                and stands[0] == expected
                and stands[1] == due + timedelta(minutes=(60 if expected == 1 else 120))
            )
    assert (await req_row(owner_engine, req))[0] == "expired"
    assert levels_seen == [1, 2, 3]


async def test_stand_in_at_level_m_satisfies_up_to_m(app_engine, owner_engine, worker_engine):
    # medium: the first level timed out, the level-2 stand-in approves: that satisfies level 1 and 2, done
    w, req, _ = await approval_world(owner_engine, app_engine)
    step, *_ = await pending_step(owner_engine, req)
    await timeouts(app_engine, worker_engine, await step_due(owner_engine, step))
    stand_in, level, role = await pending_step(owner_engine, req)
    assert (level, role) == (2, "manager")
    manager = await staff(owner_engine, w.org, "manager")
    out = await decide(app_engine, w, stand_in, manager, "manager")
    assert out["request_status"] == "approved"  # catches: the approval of a stand-in ignored
    # high: a level-2 stand-in satisfies 1 and 2, so level 3 opens next (not level 1 again)
    w2, req2, _ = await approval_world(owner_engine, app_engine, high=True)
    s1, *_ = await pending_step(owner_engine, req2)
    await timeouts(app_engine, worker_engine, await step_due(owner_engine, s1))
    s2, _, _ = await pending_step(owner_engine, req2)
    out2 = await decide(app_engine, w2, s2, await staff(owner_engine, w2.org, "manager"), "manager")
    assert out2["request_status"] == "pending_approval"
    _, next_level, next_role = await pending_step(owner_engine, req2)
    assert (next_level, next_role) == (3, "org_admin")


async def test_overdue_steps_of_a_request_that_is_no_longer_waiting_are_ignored(
    app_engine, owner_engine, worker_engine
):
    _w, req, _ = await approval_world(owner_engine, app_engine, levels=ONE)
    step, *_ = await pending_step(owner_engine, req)
    async with (
        owner_engine.begin() as c
    ):  # planted around the service: the request is cancelled, its step still pending
        await c.execute(
            text("update decision_requests set status='cancelled' where id=:r"), {"r": req["id"]}
        )
    out = await timeouts(
        app_engine, worker_engine, await step_due(owner_engine, step) + timedelta(days=1)
    )
    assert out["steps"] == 0
    assert (await fetch(owner_engine, "select status from approval_steps where id=:s", s=step))[0][
        0
    ] == "pending"


async def test_run_timeouts_uses_the_injected_clock_only(app_engine, owner_engine, worker_engine):
    # The fixtures live in January 2026 and the machine clock is later: a wall-clock leak would escalate at once.
    _w, req, _ = await approval_world(owner_engine, app_engine, levels=ONE)
    step, *_ = await pending_step(owner_engine, req)
    due = await step_due(owner_engine, step)
    assert (await timeouts(app_engine, worker_engine, FakeClock(due - timedelta(seconds=1))))[
        "steps"
    ] == 0  # catches: wall-clock leak
    assert (await req_row(owner_engine, req))[0] == "pending_approval"
    assert (await timeouts(app_engine, worker_engine, FakeClock(due)))["steps"] == 1


async def test_each_tenants_work_runs_in_its_own_tenant_context(
    app_engine, owner_engine, worker_engine
):
    a, req_a, _ = await approval_world(owner_engine, app_engine, levels=ONE)
    b, _req_b, _ = await approval_world(owner_engine, app_engine, levels=ONE)
    step_a, *_ = await pending_step(owner_engine, req_a)
    out = await timeouts(
        app_engine, worker_engine, (await step_due(owner_engine, step_a)) + timedelta(days=1)
    )
    assert out["steps"] == 2 and out["errors"] == 0
    for w in (a, b):
        events = await fetch(
            owner_engine,
            "select payload->>'decision_request_id' from outbox_events where type='step.timed_out' and organization_id=:o",
            o=w.org,
        )
        assert len(events) == 1
    ids = {
        r[0]
        for r in await fetch(
            owner_engine,
            "select payload->>'decision_request_id' from outbox_events where type='step.timed_out' and organization_id=:o",
            o=a.org,
        )
    }
    assert ids == {str(req_a["id"])}  # catches: tenant context taken from the worker, not the item


async def test_each_effect_writes_exactly_one_audit_row_and_one_event(
    app_engine, owner_engine, worker_engine
):
    two_levels = [
        {"role": "manager", "timeout_minutes": 60},
        {"role": "org_admin", "timeout_minutes": 60},
    ]
    w = await make_world(owner_engine, "pro", levels=two_levels)
    req = await open_request(app_engine, w)
    a, *_ = await voters(owner_engine, w, 3)
    await cast(app_engine, w, req, a)
    at = req["voting_deadline_at"]
    await timeouts(app_engine, worker_engine, at)
    await timeouts(app_engine, worker_engine, at)  # a second sweep adds nothing
    count = lambda sql: fetch(owner_engine, sql, o=w.org, r=req["id"])
    audit = {
        r[0]: r[1]
        for r in await count(
            "select action, count(*) from audit_logs where organization_id=:o and entity_id=:r group by 1"
        )
    }
    assert audit["escalated"] == 1 and audit["round_revealed"] == 1 and audit["created"] == 1
    assert audit["status_changed"] == 1  # pending_votes -> pending_approval, once
    events = {
        r[0]: r[1]
        for r in await count(
            "select type, count(*) from outbox_events where organization_id=:o group by 1"
        )
    }
    assert (
        events["round.revealed"] == events["decision.status_changed"] == events["step.created"] == 1
    )
    # the step side: one time-out, one stand-in
    step, *_ = await pending_step(owner_engine, req)
    due_at = await step_due(owner_engine, step)
    await timeouts(app_engine, worker_engine, due_at)
    await timeouts(app_engine, worker_engine, due_at)
    ev = {
        r[0]: r[1]
        for r in await count(
            "select type, count(*) from outbox_events where organization_id=:o group by 1"
        )
    }
    assert ev["step.timed_out"] == 1 and ev["step.created"] == 2  # level 1, then the stand-in
    assert (
        len(
            await fetch(
                owner_engine,
                "select 1 from audit_logs where organization_id=:o and action='timed_out'",
                o=w.org,
            )
        )
        == 1
    )


async def test_a_message_in_the_discussion_does_not_stop_the_deadline(
    app_engine, owner_engine, worker_engine
):
    w = await make_world(owner_engine, "pro", rounds=2, timeout=30)
    req = await open_request(app_engine, w)
    sessions = await voters(owner_engine, w, 3)
    for s, v in zip(sessions, ("approve", "approve", "reject"), strict=True):
        await cast(app_engine, w, req, s, v)
    await post(app_engine, w, req, "we are talking", session=sessions[0])
    deadline = (await req_row(owner_engine, req))[3]
    await timeouts(app_engine, worker_engine, deadline)
    status, reason, receipt, *_ = await req_row(owner_engine, req)
    assert (status, reason, receipt) == (
        "pending_approval",
        "quorum_not_reached",
        None,
    )  # a message opens round 2, it does not extend the timer


async def test_the_same_user_cannot_approve_a_stand_in_and_the_next_level(
    app_engine, owner_engine, worker_engine
):
    from app.domain.errors import AlreadyDecided

    w, req, _ = await approval_world(owner_engine, app_engine, high=True)
    s1, *_ = await pending_step(owner_engine, req)
    await timeouts(app_engine, worker_engine, await step_due(owner_engine, s1))
    s2, *_ = await pending_step(owner_engine, req)
    admin = await staff(owner_engine, w.org, "org_admin")
    await decide(
        app_engine, w, s2, admin, "org_admin"
    )  # the level-2 stand-in satisfies levels 1 and 2
    s3, level, _ = await pending_step(owner_engine, req)
    assert level == 3
    with pytest.raises(
        AlreadyDecided
    ):  # catches: one person walking the whole ladder after a timeout (FM8)
        await decide(app_engine, w, s3, admin, "org_admin")

import asyncio
from datetime import timedelta

import pytest
from sqlalchemy import text

from app.domain.errors import RoundClosed, StepNotPending

from .helpers import (
    approval_world,
    cast,
    decide,
    fetch,
    make_world,
    open_request,
    pending_step,
    staff,
    timeouts,
    voters,
)  # fmt: skip

pytestmark = [pytest.mark.anyio, pytest.mark.usefixtures("drained")]

ONE = [{"role": "manager", "timeout_minutes": 60}]


async def due(owner, step):
    return (await fetch(owner, "select due_at from approval_steps where id=:s", s=step))[0][0]


async def test_run_twice_one_effect(app_engine, owner_engine, worker_engine):
    _w, req, _ = await approval_world(owner_engine, app_engine, high=True)
    step, *_ = await pending_step(owner_engine, req)
    at = await due(owner_engine, step)
    first = await timeouts(app_engine, worker_engine, at)
    second = await timeouts(app_engine, worker_engine, at)
    assert (
        first["steps"] == 1 and second["steps"] == 0
    )  # catches: a second run creating a second stand-in
    rows = await fetch(
        owner_engine,
        "select level, status from approval_steps where decision_request_id=:r order by level",
        r=req["id"],
    )
    assert rows == [(1, "timed_out"), (2, "pending")]


async def test_two_workers_concurrent_skip_locked_one_stand_in(
    app_engine, owner_engine, worker_engine
):
    _w, req, _ = await approval_world(owner_engine, app_engine, high=True)
    step, *_ = await pending_step(owner_engine, req)
    at = await due(owner_engine, step)
    results = await asyncio.gather(*(timeouts(app_engine, worker_engine, at) for _ in range(4)))
    assert sum(r["steps"] for r in results) == 1 and all(
        r["errors"] == 0 for r in results
    )  # catches: a plain SELECT letting both act
    rows = await fetch(
        owner_engine,
        "select level, status from approval_steps where decision_request_id=:r order by level",
        r=req["id"],
    )
    assert rows == [(1, "timed_out"), (2, "pending")]
    audit = await fetch(
        owner_engine, "select 1 from audit_logs where action='timed_out' and entity_id=:s", s=step
    )
    assert len(audit) == 1


async def test_decision_and_timeout_in_the_same_instant_one_wins(
    app_engine, owner_engine, worker_engine
):
    for _ in range(3):
        w, req, _ = await approval_world(owner_engine, app_engine, levels=ONE)
        step, *_ = await pending_step(owner_engine, req)
        user = await staff(owner_engine, w.org, "manager")
        at = await due(owner_engine, step)
        decided, swept = await asyncio.gather(
            decide(app_engine, w, step, user, "manager", clock=_clock(at - timedelta(seconds=1))),
            timeouts(app_engine, worker_engine, at),
            return_exceptions=True,
        )
        step_status = (
            await fetch(owner_engine, "select status from approval_steps where id=:s", s=step)
        )[0][0]
        request = (
            await fetch(
                owner_engine,
                "select status, receipt is not null from decision_requests where id=:r",
                r=req["id"],
            )
        )[0]
        # catches: a step both approved and timed out
        if isinstance(decided, dict):  # the human got the lock first
            assert (step_status, request) == ("approved", ("approved", True))
        else:  # the job got it first: the human is refused, the request expired without a receipt
            assert isinstance(decided, StepNotPending) and (step_status, request) == (
                "timed_out",
                ("expired", False),
            )
        assert not isinstance(swept, BaseException)


async def test_a_closing_vote_and_the_voting_deadline_one_consistent_outcome(
    app_engine, owner_engine, worker_engine
):
    for _ in range(3):
        w = await make_world(owner_engine, floors={"refund": "low"})
        req = await open_request(app_engine, w)
        a, b, c = await voters(owner_engine, w, 3)
        await cast(app_engine, w, req, a)
        await cast(app_engine, w, req, b)
        deadline = req["voting_deadline_at"]
        vote, swept = await asyncio.gather(
            cast(app_engine, w, req, c, clock=_clock(deadline - timedelta(microseconds=1))),
            timeouts(app_engine, worker_engine, deadline),
            return_exceptions=True,
        )
        status, reason, receipt = (
            await fetch(
                owner_engine,
                "select status, escalation_reason, receipt is not null from decision_requests where id=:r",
                r=req["id"],
            )
        )[0]
        assert not isinstance(swept, BaseException)
        if isinstance(
            vote, dict
        ):  # the vote closed the round: approved, the job found nothing to do
            assert (status, reason, receipt) == ("approved", None, True)
        else:  # the job got there first: humans, and the late vote is refused
            assert isinstance(vote, RoundClosed) and (status, reason, receipt) == (
                "pending_approval",
                "quorum_not_reached",
                False,
            )


def _clock(at):
    from .helpers import FakeClock

    return FakeClock(at)


async def test_a_request_locked_by_someone_else_is_skipped_not_waited_for(
    app_engine, owner_engine, worker_engine
):
    # catches: a plain FOR UPDATE making the cron block behind a human decision (or a vote) in flight
    _w, req, _ = await approval_world(owner_engine, app_engine, levels=ONE)
    step, *_ = await pending_step(owner_engine, req)
    at = await due(owner_engine, step)
    async with owner_engine.connect() as holder:
        tx = await holder.begin()
        await holder.execute(
            text("select 1 from decision_requests where id=:r for update"), {"r": req["id"]}
        )
        out = await asyncio.wait_for(
            timeouts(app_engine, worker_engine, at), 5
        )  # returns, does not wait
        assert out["steps"] == 0
        await tx.commit()
    assert (await timeouts(app_engine, worker_engine, at))[
        "steps"
    ] == 1  # the next sweep does the work


async def test_a_pending_step_of_an_older_version_is_ignored(
    app_engine, owner_engine, worker_engine
):
    _w, req, _ = await approval_world(owner_engine, app_engine, levels=ONE)
    step, *_ = await pending_step(owner_engine, req)
    async with (
        owner_engine.begin() as c
    ):  # planted around revise (which supersedes the step in the same transaction)
        await c.execute(
            text("update decision_requests set version = 2 where id=:r"), {"r": req["id"]}
        )
    out = await timeouts(
        app_engine, worker_engine, await due(owner_engine, step) + timedelta(days=1)
    )
    assert out["steps"] == 0
    assert (await fetch(owner_engine, "select status from approval_steps where id=:s", s=step))[0][
        0
    ] == "pending"


async def test_a_deadline_moved_after_the_scan_is_rechecked_under_the_lock(
    app_engine, owner_engine
):
    # A revise (or entering the discussion) can push the deadline out between the scan and the lock.
    from app.db.session import tenant_session
    from app.services.escalation import escalate_voting

    w = await make_world(owner_engine)
    req = await open_request(app_engine, w)
    deadline = req["voting_deadline_at"]

    async def attempt(now):
        async with tenant_session(app_engine, w.org) as s:
            return await escalate_voting(s, w.org, req["id"], now)

    assert (
        await attempt(deadline - timedelta(seconds=1)) is False
    )  # catches: acting on a stale candidate
    assert (
        await fetch(owner_engine, "select status from decision_requests where id=:r", r=req["id"])
    )[0][0] == "pending_votes"
    assert await attempt(deadline) is True
    assert await attempt(deadline) is False  # and only once

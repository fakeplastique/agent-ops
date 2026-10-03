import asyncio

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.domain.errors import StepNotPending

from .helpers import approval_world, decide, fetch, pending_step, staff

pytestmark = pytest.mark.anyio


async def test_two_deciders_concurrent_one_wins_other_409(app_engine, owner_engine):
    w, req, _ = await approval_world(
        owner_engine, app_engine, levels=[{"role": "manager", "timeout_minutes": 60}]
    )
    step, *_ = await pending_step(owner_engine, req)
    a = await staff(owner_engine, w.org, "manager")
    b = await staff(owner_engine, w.org, "org_admin")
    results = await asyncio.gather(
        decide(app_engine, w, step, a), decide(app_engine, w, step, b), return_exceptions=True
    )
    # catches: read-then-update on a step
    assert sorted(type(r).__name__ for r in results) == ["StepNotPending", "dict"]
    winner = next(r for r in results if isinstance(r, dict))
    rows = await fetch(
        owner_engine, "select status, approver_user_id from approval_steps where id=:s", s=step
    )
    assert rows[0][0] == "approved" and rows[0][1] in (a, b)
    assert winner["request_status"] == "approved"
    assert (
        len(
            await fetch(
                owner_engine,
                "select 1 from audit_logs where entity_id=:s and action='decided'",
                s=step,
            )
        )
        == 1
    )


async def test_decide_twice_same_user_same_step_returns_first_result(app_engine, owner_engine):
    w, req, _ = await approval_world(
        owner_engine, app_engine, levels=[{"role": "manager", "timeout_minutes": 60}]
    )
    step, *_ = await pending_step(owner_engine, req)
    user = await staff(owner_engine, w.org, "manager")
    first = await decide(app_engine, w, step, user)
    again = await decide(app_engine, w, step, user)  # catches: double-apply on retry
    assert (
        again["request_status"] == first["request_status"] == "approved"
        and again["replayed"] is True
    )
    both = await asyncio.gather(
        decide(app_engine, w, step, user), decide(app_engine, w, step, user), return_exceptions=True
    )
    assert all(isinstance(r, dict) for r in both)
    assert (
        len(
            await fetch(
                owner_engine,
                "select 1 from audit_logs where entity_id=:s and action='decided'",
                s=step,
            )
        )
        == 1
    )
    assert (
        len(
            await fetch(
                owner_engine,
                "select 1 from approval_steps where decision_request_id=:r",
                r=req["id"],
            )
        )
        == 1
    )


async def test_a_late_second_decider_on_a_decided_step_is_409(app_engine, owner_engine):
    w, req, _ = await approval_world(
        owner_engine, app_engine, levels=[{"role": "manager", "timeout_minutes": 60}]
    )
    step, *_ = await pending_step(owner_engine, req)
    await decide(app_engine, w, step, await staff(owner_engine, w.org, "manager"))
    with pytest.raises(StepNotPending):
        await decide(app_engine, w, step, await staff(owner_engine, w.org, "org_admin"))


async def test_decide_holds_the_request_row_lock_before_it_touches_the_step(
    app_engine, owner_engine
):
    """The one lock order (request row first, then steps) that S12's timeout job must share.

    Another transaction holds the STEP row; decide must already own the REQUEST row while it waits."""
    w, req, _ = await approval_world(
        owner_engine, app_engine, levels=[{"role": "manager", "timeout_minutes": 60}]
    )
    step, *_ = await pending_step(owner_engine, req)
    user = await staff(owner_engine, w.org, "manager")
    async with owner_engine.connect() as holder:
        tx = await holder.begin()
        await holder.execute(
            text("select 1 from approval_steps where id=:s for update"), {"s": step}
        )
        task = asyncio.create_task(decide(app_engine, w, step, user, "manager"))
        done, _ = await asyncio.wait({task}, timeout=1.0)
        assert not done  # blocked on the step row
        async with owner_engine.connect() as probe:
            with pytest.raises(
                DBAPIError
            ):  # catches: deciding without taking the request lock first
                await probe.execute(
                    text("select 1 from decision_requests where id=:r for update nowait"),
                    {"r": req["id"]},
                )
        await tx.commit()
    assert (await asyncio.wait_for(task, 10))["request_status"] == "approved"

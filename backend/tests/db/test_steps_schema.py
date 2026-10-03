import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from tests.integration.helpers import approval_world, fetch, pending_step, staff

pytestmark = pytest.mark.anyio


async def refused(owner, sql, **p) -> bool:
    try:
        async with owner.begin() as c:
            await c.execute(text(sql), p)
    except DBAPIError:
        return True
    return False


INSERT = (
    "insert into approval_steps (organization_id, decision_request_id, request_version, level, required_role, status, due_at)"
    " values (:o, :r, :v, :l, :role, 'pending', now())"
)


async def test_at_most_one_pending_step_per_request_version(app_engine, owner_engine):
    w, req, _ = await approval_world(owner_engine, app_engine)
    args = {"o": w.org, "r": req["id"], "role": "manager"}
    # catches: two steps pending at once (one-pending-at-a-time as a code rule only)
    assert await refused(owner_engine, INSERT, v=1, l=2, **args)
    assert not await refused(
        owner_engine, INSERT, v=2, l=1, **args
    )  # another version has its own chain


async def test_one_step_per_level_and_version(app_engine, owner_engine):
    w, req, _ = await approval_world(owner_engine, app_engine)
    async with owner_engine.begin() as c:
        await c.execute(
            text("update approval_steps set status='timed_out' where decision_request_id=:r"),
            {"r": req["id"]},
        )
    args = {"o": w.org, "r": req["id"], "role": "manager"}
    assert await refused(
        owner_engine, INSERT, v=1, l=1, **args
    )  # level 1 already exists for version 1
    assert not await refused(owner_engine, INSERT, v=1, l=2, **args)


async def test_one_user_decides_one_step_per_version_in_the_index(app_engine, owner_engine):
    w, req, _ = await approval_world(owner_engine, app_engine, high=True)
    user = await staff(owner_engine, w.org, "org_admin")
    step1, *_ = await pending_step(owner_engine, req)
    decide = "update approval_steps set status='approved', approver_user_id=:u, decided_at=now() where id=:s"
    assert not await refused(owner_engine, decide, u=user, s=step1)
    async with owner_engine.begin() as c:  # a second pending step planted around the service
        await c.execute(
            text(INSERT), {"o": w.org, "r": req["id"], "v": 1, "l": 2, "role": "manager"}
        )
    step2 = (
        await fetch(
            owner_engine,
            "select id from approval_steps where decision_request_id=:r and level=2",
            r=req["id"],
        )
    )[0][0]
    # catches: the rule enforced only in code (D15)
    assert await refused(owner_engine, decide, u=user, s=step2)
    other = await staff(owner_engine, w.org, "org_admin")
    assert not await refused(owner_engine, decide, u=other, s=step2)


async def test_decided_steps_are_immutable_and_decisions_are_complete(app_engine, owner_engine):
    w, req, _ = await approval_world(owner_engine, app_engine)
    user = await staff(owner_engine, w.org, "org_admin")
    step, *_ = await pending_step(owner_engine, req)
    assert await refused(
        owner_engine, "update approval_steps set status='approved' where id=:s", s=step
    )  # no approver, no decided_at
    assert await refused(
        owner_engine,
        "update approval_steps set status='approved', approver_user_id=:u where id=:s",
        u=user,
        s=step,
    )
    ok = "update approval_steps set status='approved', approver_user_id=:u, decided_at=now() where id=:s"
    assert not await refused(owner_engine, ok, u=user, s=step)
    for sql in (
        "update approval_steps set status='rejected' where id=:s",
        "update approval_steps set comment='x' where id=:s",
        "update approval_steps set status='pending' where id=:s",
    ):
        assert await refused(owner_engine, sql, s=step), (
            sql
        )  # catches: editing a decision after the fact


async def test_step_constraints_and_tenancy(app_engine, owner_engine):
    w, req, _ = await approval_world(owner_engine, app_engine)
    other = await approval_world(owner_engine, app_engine)
    base = {"o": w.org, "r": req["id"], "v": 2, "l": 1, "role": "manager"}
    for change in ({"role": "member"}, {"role": "root"}, {"l": 0}, {"v": 0}):
        assert await refused(owner_engine, INSERT, **base | change), change
    assert await refused(
        owner_engine, INSERT, **base | {"o": other[0].org}
    )  # composite FK: the request is not theirs
    rls = await fetch(
        owner_engine,
        "select relrowsecurity, relforcerowsecurity from pg_class where relname='approval_steps'",
    )
    assert rls == [(True, True)]

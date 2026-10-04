import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from tests.integration.helpers import approval_world, fetch, make_world, open_request

pytestmark = pytest.mark.anyio

ONE = [{"role": "manager", "timeout_minutes": 60}]


async def worker_sql(engine, sql, **p):
    async with engine.connect() as c:
        return (await c.execute(text(sql), p)).all()


async def refused(engine, sql, **p) -> bool:
    try:
        async with engine.begin() as c:
            await c.execute(text(sql), p)
    except DBAPIError:
        return True
    return False


async def test_the_worker_role_is_not_privileged_and_is_nologin_outside_tests(owner_engine):
    row = (
        await fetch(
            owner_engine,
            "select rolsuper, rolbypassrls, rolcreatedb, rolcreaterole from pg_roles where rolname='app_worker'",
        )
    )[0]
    assert row == (False, False, False, False)
    owns = await fetch(
        owner_engine,
        "select count(*) from pg_class c join pg_roles r on r.oid=c.relowner where r.rolname='app_worker'",
    )
    assert owns[0][0] == 0


async def test_the_worker_sees_ids_of_due_work_across_tenants_and_nothing_else(
    app_engine, owner_engine, worker_engine
):
    a, _req_a, _ = await approval_world(owner_engine, app_engine, levels=ONE)
    b = await make_world(owner_engine)
    req_b = await open_request(app_engine, b)
    steps = await worker_sql(
        worker_engine, "select organization_id, id from approval_steps where status = 'pending'"
    )
    assert a.org in {s[0] for s in steps}
    reqs = {
        r[0]
        for r in await worker_sql(
            worker_engine,
            "select organization_id from decision_requests where status in ('pending_votes', 'pending_discussion')",
        )
    }
    assert b.org in reqs and a.org not in reqs  # a's request is waiting for humans, not for votes
    # catches: a worker that can read what a request says or who decided it
    for sql in (
        "select title from decision_requests",
        "select payload from decision_requests",
        "select policy_snapshot from decision_requests",
        "select approver_user_id from approval_steps",
        "select comment from approval_steps",
        "select * from votes",
        "select * from messages",
        "select * from audit_logs",
        "select * from memberships",
        "select * from organizations",
    ):
        assert await refused(worker_engine, sql), sql
    assert req_b["id"]


async def test_the_worker_cannot_change_anything_and_does_not_see_decided_rows(
    app_engine, owner_engine, worker_engine
):
    _a, req_a, _ = await approval_world(owner_engine, app_engine, levels=ONE)
    for sql in (
        "update approval_steps set status='approved'",
        "update decision_requests set status='approved'",
        "delete from approval_steps",
        "insert into approval_steps (organization_id) values (gen_random_uuid())",
    ):
        assert await refused(worker_engine, sql), sql
    async with owner_engine.begin() as c:
        await c.execute(
            text("update approval_steps set status='timed_out' where decision_request_id=:r"),
            {"r": req_a["id"]},
        )
    rows = await worker_sql(
        worker_engine, "select id from approval_steps where decision_request_id=:r", r=req_a["id"]
    )
    assert rows == []  # the scan policy only exposes pending steps

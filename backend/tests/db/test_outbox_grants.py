import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.db.session import tenant_session
from app.services import outbox
from tests.integration.helpers import fetch, make_org

pytestmark = pytest.mark.anyio


async def refused(engine, sql, **p) -> bool:
    try:
        async with engine.begin() as c:
            await c.execute(text(sql), p)
    except DBAPIError:
        return True
    return False


async def as_tenant_refused(engine, org, sql, **p) -> bool:
    try:
        async with tenant_session(engine, org) as s:
            await s.execute(text(sql), p)
    except DBAPIError:
        return True
    return False


async def test_the_runtime_role_can_append_events_but_not_edit_or_delete_them(
    app_engine, owner_engine
):
    org = await make_org(owner_engine)
    async with tenant_session(app_engine, org) as s:
        await outbox.emit(s, org, "decision.created", {"k": 1})
    # catches: a tenant rewriting history (or marking its own events published) around the service
    for sql in (
        "update outbox_events set payload='{}'",
        "update outbox_events set published_at=now()",
        "delete from outbox_events",
    ):
        assert await as_tenant_refused(app_engine, org, sql), sql


async def test_the_event_counter_only_moves_up_by_one(app_engine, owner_engine):
    org = await make_org(owner_engine)
    async with tenant_session(app_engine, org) as s:
        await outbox.emit(s, org, "decision.created", {})
        await outbox.emit(s, org, "decision.created", {})
    for sql in (
        "update org_event_counters set seq = 0",
        "update org_event_counters set seq = seq + 5",
        "delete from org_event_counters",
    ):
        assert await as_tenant_refused(app_engine, org, sql), (
            sql
        )  # catches: rewinding seq (duplicate or reordered events)
    async with tenant_session(app_engine, org) as s:
        assert await outbox.emit(s, org, "decision.created", {}) == 3


async def test_the_worker_marks_events_published_and_nothing_else(
    app_engine, owner_engine, worker_engine
):
    org = await make_org(owner_engine)
    async with tenant_session(app_engine, org) as s:
        await outbox.emit(s, org, "decision.created", {"secret": "x"})
    rows = await fetch(owner_engine, "select id from outbox_events where organization_id=:o", o=org)
    async with worker_engine.connect() as c:
        seen = (
            await c.execute(
                text(
                    "select id, organization_id, seq, type from outbox_events where organization_id=:o"
                ),
                {"o": org},
            )
        ).all()
    assert len(seen) == 1 and seen[0][0] == rows[0][0]
    for sql in (
        "select payload from outbox_events",
        "select * from outbox_events",
        "delete from outbox_events",
        "update outbox_events set type='x'",
        "update outbox_events set seq=9",
        "insert into outbox_events (organization_id, seq, type, payload) values (gen_random_uuid(), 1, 'x', '{}')",
    ):
        assert await refused(worker_engine, sql), sql
    assert not await refused(
        worker_engine,
        "update outbox_events set published_at = now() where organization_id=:o",
        o=org,
    )
    async with worker_engine.begin() as c:  # once published, always: the row is no longer updatable
        res = await c.execute(
            text("update outbox_events set published_at = null where organization_id=:o"),
            {"o": org},
        )
        assert res.rowcount == 0
    async with worker_engine.connect() as c:  # it sees the stamp, still no payload
        stamped = (
            await c.execute(
                text("select published_at from outbox_events where organization_id=:o"), {"o": org}
            )
        ).all()
    assert stamped[0][0] is not None


async def test_a_published_event_is_frozen_even_for_the_owner(app_engine, owner_engine):
    org = await make_org(owner_engine)
    async with tenant_session(app_engine, org) as s:
        await outbox.emit(s, org, "decision.created", {})
    await refused(
        owner_engine,
        "update outbox_events set published_at = now() where organization_id=:o",
        o=org,
    )
    for sql in (
        "update outbox_events set published_at = null where organization_id=:o",
        "update outbox_events set payload='{}' where organization_id=:o",
        "update outbox_events set type='x' where organization_id=:o",
    ):
        assert await refused(owner_engine, sql, o=org), sql

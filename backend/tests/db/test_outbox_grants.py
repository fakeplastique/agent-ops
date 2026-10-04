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


async def test_an_event_is_born_unpublished_and_tied_to_the_counter(app_engine, owner_engine):
    org = await make_org(owner_engine)
    async with tenant_session(app_engine, org) as s:
        await outbox.emit(s, org, "decision.created", {})  # seq 1, counter 1
    ins = "insert into outbox_events (organization_id, seq, type, payload{extra}) values (:o, :seq, 'x', '{{}}'{val})"
    # catches: a pre-stamped event (the relay would never send it) or a seq detached from the counter
    assert await as_tenant_refused(
        app_engine, org, ins.format(extra=", published_at", val=", now()"), o=org, seq=2
    )
    assert await as_tenant_refused(app_engine, org, ins.format(extra="", val=""), o=org, seq=999999)
    assert await as_tenant_refused(
        app_engine, org, ins.format(extra="", val=""), o=org, seq=2
    )  # counter is still 1
    async with tenant_session(app_engine, org) as s:
        assert (
            await outbox.emit(s, org, "decision.created", {}) == 2
        )  # the normal path is untouched


async def test_the_counter_cannot_skip_a_number_or_start_high(app_engine, owner_engine):
    org, fresh = await make_org(owner_engine), await make_org(owner_engine)
    async with tenant_session(app_engine, org) as s:
        await outbox.emit(s, org, "decision.created", {})
    # a bump without its event leaves a gap: refused when the transaction commits
    assert await as_tenant_refused(app_engine, org, "update org_event_counters set seq = seq + 1")
    # a new tenant must start at 1, not at a hole
    assert await as_tenant_refused(
        app_engine,
        fresh,
        "insert into org_event_counters (organization_id, seq) values (:o, 1000)",
        o=fresh,
    )
    async with tenant_session(app_engine, org) as s:
        assert await outbox.emit(s, org, "decision.created", {}) == 2
    async with tenant_session(app_engine, fresh) as s:
        assert await outbox.emit(s, fresh, "decision.created", {}) == 1


async def test_a_counter_and_event_pair_cannot_open_the_sequence_at_a_hole(
    app_engine, owner_engine
):
    # Satisfies the other checks (a matching event exists, the seq equals the counter) but starts at 1000.
    org = await make_org(owner_engine)
    try:
        async with tenant_session(app_engine, org) as s:
            await s.execute(
                text("insert into org_event_counters (organization_id, seq) values (:o, 1000)"),
                {"o": org},
            )
            await s.execute(
                text(
                    "insert into outbox_events (organization_id, seq, type, payload) values (:o, 1000, 'x', '{}')"
                ),
                {"o": org},
            )
    except DBAPIError:
        pass
    else:
        raise AssertionError(
            "a sequence opened at seq 1000"
        )  # catches: a hole at the start of a tenant's stream
    async with tenant_session(app_engine, org) as s:
        assert await outbox.emit(s, org, "decision.created", {}) == 1

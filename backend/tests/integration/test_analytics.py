"""S34: the analytics consumer turns outbox events into Analytics events that carry ids and enums only."""

import uuid

import pytest
from redis.asyncio import Redis

from app.db.session import tenant_session
from app.services import plans
from app.worker.analytics import analytics_job, check_props
from tests.conftest import TEST_REDIS
from tests.integration.helpers import (
    LEVELS3,
    FakeClock,
    cast,
    decide,
    fetch,
    make_world,
    open_request,
    pending_step,
    staff,
    timeouts,
    utc,
    voters,
)

pytestmark = pytest.mark.anyio

SECRET = "Refund jane.doe@example.test SECRET-TITLE"


class Recorder:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict]] = []

    def emit(self, name: str, props) -> None:
        self.events.append((name, dict(props)))


@pytest.fixture
async def redis():
    r = Redis.from_url(TEST_REDIS)
    await r.flushdb()
    yield r
    await r.flushdb()
    await r.aclose()


async def _run_all(ctx, owner, org) -> None:
    for (event_id,) in await fetch(
        owner, "select id from outbox_events where organization_id=:o order by seq", o=org
    ):
        await analytics_job(ctx, str(org), str(event_id))


async def test_events_carry_ids_and_enums_only(app_engine, owner_engine, worker_engine, redis):
    w = await make_world(
        owner_engine, "pro", floors={"refund": "medium", "note": "low"}, levels=LEVELS3
    )
    secret = {"title": SECRET, "description": SECRET, "payload": {"email": "jane.doe@example.test"}}
    escalated = await open_request(app_engine, w, **secret)
    auto = await open_request(app_engine, w, action_type="note", **secret)
    for s in await voters(owner_engine, w, 3):
        await cast(app_engine, w, escalated, s, rationale=SECRET)
        await cast(app_engine, w, auto, s, rationale=SECRET)
    await timeouts(
        app_engine, worker_engine, utc(2026, 1, 15, 10, 45)
    )  # level 1 (30 min) times out
    # A human approves the stand-in step: approved, but not auto-approved.
    step, *_ = await pending_step(owner_engine, escalated)
    later = FakeClock(utc(2026, 1, 15, 10, 46))
    await decide(app_engine, w, step, await staff(owner_engine, w.org, "org_admin"), clock=later)
    assert await fetch(
        owner_engine, "select status from decision_requests where id=:r", r=escalated["id"]
    ) == [("approved",)]
    async with tenant_session(app_engine, w.org) as s:
        await plans.change_plan(s, w.org, "enterprise", w.owner_user, "platform")

    sink = Recorder()
    ctx = {"work_engine": app_engine, "redis": redis, "analytics": sink}
    await _run_all(ctx, owner_engine, w.org)
    names = [n for n, _ in sink.events]
    assert sorted(set(names)) == [
        "decision_auto_approved", "decision_created", "plan_changed", "step_escalated",
    ]  # fmt: skip
    assert names.count("decision_created") == 2 and names.count("decision_auto_approved") == 1
    auto_props = next(p for n, p in sink.events if n == "decision_auto_approved")
    assert auto_props["decision_request_id"] == str(auto["id"])
    plan = next(p for n, p in sink.events if n == "plan_changed")
    assert set(plan) == {"event_id", "organization_id", "from", "to"}
    assert (plan["from"], plan["to"]) == ("pro", "enterprise")
    # catches: PII in analytics (title, description, payload, rationale, action type, email)
    dumped = repr(sink.events)
    for leak in ("SECRET", "jane", "@", "refund", "note", "Refund"):
        assert leak not in dumped, leak
    for _, props in sink.events:
        check_props(props)
        assert props["organization_id"] == str(w.org)

    # At-least-once delivery: a job that runs again for the same event emits nothing new.
    before = len(sink.events)
    await _run_all(ctx, owner_engine, w.org)
    assert len(sink.events) == before


async def test_a_foreign_event_id_reads_nothing(app_engine, owner_engine, redis):
    # The job reads the event inside the named tenant: another org's event id is not found there.
    a = await make_world(owner_engine, "pro")
    b = await make_world(owner_engine, "pro")
    await open_request(app_engine, a)
    [(event_id,)] = await fetch(
        owner_engine, "select id from outbox_events where organization_id=:o", o=a.org
    )
    sink = Recorder()
    # The owner role skips RLS: only the query's own organization filter keeps A's event out.
    for engine in (app_engine, owner_engine):
        ctx = {"work_engine": engine, "redis": redis, "analytics": sink}
        assert await analytics_job(ctx, str(b.org), str(event_id)) == "missing"
    assert sink.events == []


def test_check_props_refuses_free_text():
    check_props({"decision_request_id": str(uuid.uuid4()), "risk_level": "low", "level": 2})
    for bad in ("Refund order 7", "jane@example.test", 0.5, ["x"], {"a": 1}):
        with pytest.raises(ValueError):
            check_props({"x": bad})

"""S34: /stats/summary and /stats/timeseries (Pro, `analytics` flag). FM4: aggregates never count another tenant."""

from datetime import timedelta

import pytest

from app.db.session import tenant_session
from app.services import cache, plans, stats
from tests.api.harness import agent_auth, user_auth
from tests.integration.helpers import (
    _exec,
    cast,
    decide,
    fetch,
    make_world,
    open_request,
    pending_step,
    staff,
    utc,
    voters,
)
from tests.integration.test_cache import cached  # noqa: F401 (the fixture)

pytestmark = pytest.mark.anyio

SUMMARY = "/api/v1/stats/summary"
SERIES = "/api/v1/stats/timeseries"
T0 = utc(2026, 1, 14, 9, 0)  # a day before the web clock (2026-01-15 10:00)
STATUSES = {
    "pending_votes", "pending_discussion", "pending_approval", "approved", "rejected", "expired",
    "cancelled",
}  # fmt: skip


async def _plant(owner, req, created, decided=None):
    # The service stamps created_at by the database clock and decided_at by the test clock: pin both.
    await _exec(
        owner, "update decision_requests set created_at=:c, decided_at=:d where id=:r",
        c=created, d=decided, r=req["id"],
    )  # fmt: skip


async def _busy_org(owner, app_engine):
    """Pro org with one request of each outcome: auto-approved (60 s), rejected by votes (180 s),
    approved by a human (300 s), and one still blind in round 1 with a single vote."""
    w = await make_world(owner, "pro", floors={"refund": "medium", "note": "low"})
    a, b, c = await voters(owner, w, 3)
    auto = await open_request(app_engine, w, action_type="note")
    rejected = await open_request(app_engine, w, action_type="note")
    human = await open_request(app_engine, w)
    blind = await open_request(app_engine, w, action_type="note")
    for s in (a, b, c):
        await cast(app_engine, w, auto, s)
        await cast(app_engine, w, rejected, s, verdict="reject")
        await cast(app_engine, w, human, s)
    await cast(app_engine, w, blind, a, verdict="reject")
    step, _, _ = await pending_step(owner, human)
    await decide(app_engine, w, step, await staff(owner, w.org, "org_admin"))
    rows = await fetch(
        owner, "select id, status from decision_requests where organization_id=:o", o=w.org
    )
    got = {r: s for r, s in rows}
    assert [got[x["id"]] for x in (auto, rejected, human, blind)] == [
        "approved", "rejected", "approved", "pending_votes",
    ]  # fmt: skip
    for req, secs in ((auto, 60), (rejected, 180), (human, 300)):
        await _plant(owner, req, T0, T0 + timedelta(seconds=secs))
    await _plant(owner, blind, T0)
    return w, (a, b, c)


async def test_counts_exclude_other_tenants_including_zero_activity_org(
    web, app_engine, owner_engine
):
    a, _ = await _busy_org(owner_engine, app_engine)
    b = await make_world(owner_engine, "pro")  # same plan, zero activity
    bh = await user_auth(app_engine, b.org, b.owner_user)
    r = await web.client.get(SUMMARY, headers=bh)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["requests"] == 0 and body["votes_per_agent"] == []
    assert body["by_status"] == dict.fromkeys(STATUSES, 0)
    assert body["by_risk"] == {"low": 0, "medium": 0, "high": 0}
    series = (await web.client.get(SERIES, headers=bh)).json()
    assert len(series["points"]) == 30
    assert all(p["requests"] == p["approved"] == p["rejected"] == 0 for p in series["points"])

    # catches: an aggregate that leans on RLS alone. The owner role bypasses RLS, so only the
    # query's own organization filter can keep A's rows out of B's numbers.
    async with tenant_session(owner_engine, b.org) as s:
        raw = await stats.summary(s, b.org, web.clock, 30)
        raw_series = await stats.timeseries(s, b.org, web.clock, 30)
    assert raw["requests"] == 0 and raw["votes_per_agent"] == []
    assert sum(p["requests"] + p["approved"] for p in raw_series["points"]) == 0

    ah = await user_auth(app_engine, a.org, a.owner_user)
    mine = (await web.client.get(SUMMARY, headers=ah)).json()
    assert mine["requests"] == 4
    assert mine["by_status"]["approved"] == 2 and mine["by_status"]["pending_votes"] == 1
    assert mine["by_risk"] == {"low": 3, "medium": 1, "high": 0}
    # Revealed votes only: the blind request's single round-1 vote is not counted (I7).
    assert sorted(v["votes"] for v in mine["votes_per_agent"]) == [3, 3, 3]
    day = next(p for p in (await web.client.get(SERIES, headers=ah)).json()["points"]
               if p["date"] == "2026-01-14")  # fmt: skip
    assert day == {
        "date": "2026-01-14", "requests": 4, "approved": 2, "rejected": 1, "auto_approved": 1,
    }  # fmt: skip
    # An agent of the org reads the same numbers.
    agent = await agent_auth(app_engine, a.org, a.identity)
    assert (await web.client.get(SUMMARY, headers=agent)).json()["requests"] == 4


async def test_auto_approval_rate_and_median_time_with_no_data_are_null_not_zero_divide(
    web, app_engine, owner_engine
):
    empty = await make_world(owner_engine, "pro")
    h = await user_auth(app_engine, empty.org, empty.owner_user)
    r = await web.client.get(SUMMARY, headers=h)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["auto_approval_rate"] is None
    assert body["median_seconds_to_decision"] is None
    assert body["average_seconds_to_decision"] is None

    w, _ = await _busy_org(owner_engine, app_engine)
    h = await user_auth(app_engine, w.org, w.owner_user)
    body = (await web.client.get(SUMMARY, headers=h)).json()
    assert body["auto_approval_rate"] == pytest.approx(1 / 3)  # one auto of three decided
    assert body["median_seconds_to_decision"] == 180
    assert body["average_seconds_to_decision"] == 180

    # Outside the window: a request from 40 days back is in a 60-day window only.
    long_ago = web.clock.at - timedelta(days=40)
    await _exec(owner_engine, "update decision_requests set created_at=:c where organization_id=:o",
                c=long_ago, o=w.org)  # fmt: skip
    assert (await web.client.get(SUMMARY, headers=h)).json()["requests"] == 0
    assert (await web.client.get(SUMMARY, params={"days": 60}, headers=h)).json()["requests"] == 4
    for bad in (0, 367, "x"):
        assert (await web.client.get(SUMMARY, params={"days": bad}, headers=h)).status_code == 422


async def test_free_402(web, app_engine, owner_engine):
    w = await make_world(owner_engine, "free")
    h = await user_auth(app_engine, w.org, w.owner_user)
    for path in (SUMMARY, SERIES):
        r = await web.client.get(path, headers=h)
        assert r.status_code == 402, r.text
        assert r.json()["error"]["code"] == "plan_feature_required"
        assert r.json()["error"]["details"]["flag"] == "analytics"
    assert (await web.client.get(SUMMARY)).status_code == 401


@pytest.mark.usefixtures("cached")
async def test_stats_are_cached_per_org_and_dropped_with_the_org_stamp(
    web, app_engine, owner_engine
):
    a, _ = await _busy_org(owner_engine, app_engine)
    b = await make_world(owner_engine, "pro")
    ah = await user_auth(app_engine, a.org, a.owner_user)
    bh = await user_auth(app_engine, b.org, b.owner_user)
    assert (await web.client.get(SUMMARY, headers=ah)).json()["requests"] == 4
    assert (await web.client.get(SUMMARY, headers=ah)).json()["requests"] == 4
    assert cache.HITS["stats_summary"] == 1 and cache.MISSES["stats_summary"] == 1
    # catches: a stats key without the organization (B would be served A's warm entry)
    assert (await web.client.get(SUMMARY, headers=bh)).json()["requests"] == 0
    # A plan change replaces the org's stamp: the next read loads again and sees the newer rows.
    # Decision activity does not invalidate (it would evict every flag on each vote): within the TTL
    # a new request is not counted yet.
    await _plant(owner_engine, await open_request(app_engine, a, action_type="note"), T0)
    assert (await web.client.get(SUMMARY, headers=ah)).json()["requests"] == 4
    async with tenant_session(app_engine, a.org) as s:
        await plans.change_plan(s, a.org, "enterprise", a.owner_user, "platform")
    assert (await web.client.get(SUMMARY, headers=ah)).json()["requests"] == 5

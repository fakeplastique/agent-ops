import uuid
from datetime import UTC, datetime, time, timedelta
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.statuses import Risk, Status
from app.ports.clock import Clock
from app.services import cache

# Every query names the organization itself: RLS is the second wall, never the only one (FM4).
_WINDOW = "organization_id = :o and created_at >= :start and created_at < :end"
_AUTO = "status = 'approved' and receipt #>> '{payload,approvers}' = 'auto'"
_COUNTS = text(
    f"select status, effective_risk_level, count(*) from decision_requests where {_WINDOW}"
    " group by 1, 2"
)
_SECONDS = "extract(epoch from decided_at - created_at)"
_OUTCOMES = text(
    f"select count(*) filter (where {_AUTO}),"
    " count(*) filter (where status in ('approved', 'rejected', 'expired')),"
    f" percentile_cont(0.5) within group (order by {_SECONDS}) filter (where decided_at is not null),"
    f" avg({_SECONDS}) filter (where decided_at is not null)"
    f" from decision_requests where {_WINDOW}"
)
# Votes on requests of the window; a round-1 vote counts only once its round is revealed (blind rounds, I7).
_VOTES = text(
    "select i.id, i.name, count(*) from votes v"
    " join decision_requests d on d.organization_id = v.organization_id and d.id = v.decision_request_id"
    " join agent_sessions s on s.organization_id = v.organization_id and s.id = v.session_id"
    " join agent_identities i on i.organization_id = v.organization_id and i.id = s.agent_identity_id"
    " where v.organization_id = :o and d.created_at >= :start and d.created_at < :end"
    " and (v.round = 2 or v.revealed_at is not null)"
    " group by i.id, i.name order by count(*) desc, i.name, i.id"
)
# Requests by the UTC day they were created, outcomes by the UTC day they were decided.
_SERIES = text(
    "with days as (select generate_series(cast(:first as date), cast(:last as date), interval '1 day')::date"
    "  as day),"
    f" made as (select (created_at at time zone 'UTC')::date as day, count(*) as n from decision_requests"
    f"  where {_WINDOW} group by 1),"
    " done as (select (decided_at at time zone 'UTC')::date as day,"
    "  count(*) filter (where status = 'approved') as approved,"
    "  count(*) filter (where status = 'rejected') as rejected,"
    f"  count(*) filter (where {_AUTO}) as auto from decision_requests"
    "  where organization_id = :o and decided_at >= :start and decided_at < :end group by 1)"
    " select days.day, coalesce(made.n, 0), coalesce(done.approved, 0), coalesce(done.rejected, 0),"
    " coalesce(done.auto, 0) from days left join made using (day) left join done using (day)"
    " order by days.day"
)


def _window(o: uuid.UUID, clock: Clock, days: int) -> dict[str, Any]:
    """The last `days` whole UTC days, today included."""
    last = clock.now().astimezone(UTC).date()
    first = last - timedelta(days=days - 1)
    start = datetime.combine(first, time(), UTC)
    return {"o": o, "first": first, "last": last, "start": start, "end": start + timedelta(days)}


async def summary(session: AsyncSession, o: uuid.UUID, clock: Clock, days: int) -> dict[str, Any]:
    """Counts by status and effective risk, auto-approval rate, time to decision and revealed votes per agent
    for requests created in the window. Rates and times are None when nothing was decided (never 0/0)."""
    p = _window(o, clock, days)

    async def load() -> dict[str, Any]:
        by_status = dict.fromkeys((s.value for s in Status), 0)
        by_risk = dict.fromkeys((r.name.lower() for r in Risk), 0)
        for status, risk, n in await session.execute(_COUNTS, p):
            by_status[status] += n
            by_risk[risk] += n
        auto, decided, median, avg = (await session.execute(_OUTCOMES, p)).one()
        votes = await session.execute(_VOTES, p)
        return {
            "first_day": p["first"].isoformat(), "last_day": p["last"].isoformat(), "days": days,
            "requests": sum(by_status.values()), "by_status": by_status, "by_risk": by_risk,
            "auto_approval_rate": auto / decided if decided else None,
            "median_seconds_to_decision": None if median is None else float(median),
            "average_seconds_to_decision": None if avg is None else float(avg),
            "votes_per_agent": [
                {"agent_id": str(i), "name": name, "votes": n} for i, name, n in votes
            ],
        }  # fmt: skip

    return await cache.get_or_load(session, o, "stats_summary", f"{p['last']}:{days}", load)


async def timeseries(
    session: AsyncSession, o: uuid.UUID, clock: Clock, days: int
) -> dict[str, Any]:
    """One point per UTC day of the window, zeros included: requests created, and approved, rejected and
    auto-approved by the day they were decided."""
    p = _window(o, clock, days)

    async def load() -> dict[str, Any]:
        rows = await session.execute(_SERIES, p)
        return {
            "first_day": p["first"].isoformat(), "last_day": p["last"].isoformat(), "days": days,
            "points": [
                {"date": d.isoformat(), "requests": n, "approved": ap, "rejected": rj,
                 "auto_approved": au}
                for d, n, ap, rj, au in rows
            ],
        }  # fmt: skip

    return await cache.get_or_load(session, o, "stats_timeseries", f"{p['last']}:{days}", load)

import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.domain.statuses import Status
from app.domain.transitions import TABLE
from tests.integration.helpers import make_world, open_request, voters

pytestmark = pytest.mark.anyio

# The receipt moves with the status, so only the trigger (not the receipt CHECK) is under test.
SET_STATUS = (
    "update decision_requests set status=:s, receipt = case when :s = 'approved'"
    ' then cast(\'{"sig": "x"}\' as jsonb) end where id=:r'
)


async def refused(owner, sql, **p) -> bool:
    try:
        async with owner.begin() as c:
            await c.execute(text(sql), p)
    except DBAPIError:
        return True
    return False


async def test_status_guard_allows_exactly_the_transition_table(app_engine, owner_engine):
    """Every (from, to) pair of statuses, against the domain table: the trigger is I3's second wall."""
    allowed = {(a, b) for (a, _), b in TABLE.items()}
    w = await make_world(owner_engine)
    for old in Status:
        for new in Status:
            if old == new:
                continue
            req = await open_request(app_engine, w)
            async with (
                owner_engine.begin() as c
            ):  # plant the starting status without the guard path
                await c.execute(
                    text("alter table decision_requests disable trigger decision_status_guard")
                )
                await c.execute(
                    text(SET_STATUS),
                    {"s": old.value, "r": req["id"]},
                )
                await c.execute(
                    text("alter table decision_requests enable trigger decision_status_guard")
                )
            bad = await refused(
                owner_engine,
                SET_STATUS,
                s=new.value,
                r=req["id"],
            )
            assert bad == ((old, new) not in allowed), (
                old,
                new,
            )  # catches: a transition the table forbids


async def test_votes_constraints(app_engine, owner_engine):
    w = await make_world(owner_engine)
    req = await open_request(app_engine, w)
    (v,) = await voters(owner_engine, w, 1)
    sql = (
        "insert into votes (organization_id, decision_request_id, session_id, round, verdict, confidence, rationale, request_version)"
        " values (:o, :r, :s, :rd, :vd, :c, :ra, :ver)"
    )
    ok = {
        "o": w.org,
        "r": req["id"],
        "s": v,
        "rd": 1,
        "vd": "approve",
        "c": 0.9,
        "ra": "x",
        "ver": 1,
    }
    for change in (
        {"rd": 3},
        {"rd": 0},
        {"vd": "maybe"},
        {"c": 1.5},
        {"c": -0.1},
        {"ra": "x" * 4097},
        {"ver": 0},
    ):
        assert await refused(owner_engine, sql, **ok | change), (
            change
        )  # catches: validation only in Python
    assert not await refused(owner_engine, sql, **ok)
    assert await refused(owner_engine, sql, **ok)  # one vote per (request, version, session, round)
    assert not await refused(owner_engine, sql, **ok | {"ver": 2})  # a revised version votes afresh
    assert not await refused(owner_engine, sql, **ok | {"rd": 2})


async def test_votes_cannot_cross_tenants_or_be_edited(app_engine, owner_engine):
    a, b = await make_world(owner_engine), await make_world(owner_engine)
    req = await open_request(app_engine, a)
    (va,) = await voters(owner_engine, a, 1)
    sql = (
        "insert into votes (organization_id, decision_request_id, session_id, round, verdict, confidence, request_version)"
        " values (:o, :r, :s, 1, 'approve', 0.9, 1)"
    )
    assert await refused(
        owner_engine, sql, o=b.org, r=req["id"], s=va
    )  # composite FK: request is A's
    assert await refused(owner_engine, sql, o=a.org, r=req["id"], s=uuid.uuid4())  # unknown session


async def test_receipt_and_approved_status_go_together(app_engine, owner_engine):
    w = await make_world(owner_engine)
    req = await open_request(app_engine, w)
    forged = 'update decision_requests set receipt = cast(\'{"sig": "x"}\' as jsonb) where id=:r'
    assert await refused(
        owner_engine, forged, r=req["id"]
    )  # catches: a receipt on a pending request
    approve = "update decision_requests set status='approved' where id=:r"
    assert await refused(owner_engine, approve, r=req["id"])  # approved without a receipt
    both = "update decision_requests set status='approved', receipt = cast('{\"sig\": \"x\"}' as jsonb) where id=:r"
    assert not await refused(owner_engine, both, r=req["id"])

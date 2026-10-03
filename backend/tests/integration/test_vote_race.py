import asyncio

import pytest

from app.domain.errors import AlreadyVoted, RoundClosed

from .helpers import cast, fetch, make_world, open_request, voters

pytestmark = pytest.mark.anyio

LOW = {"refund": "low"}


async def test_double_vote_concurrent_makes_one_row(app_engine, owner_engine):
    w = await make_world(owner_engine, floors=LOW)
    req = await open_request(app_engine, w)
    (v,) = await voters(owner_engine, w, 1)
    results = await asyncio.gather(
        cast(app_engine, w, req, v), cast(app_engine, w, req, v), return_exceptions=True
    )
    assert sorted(type(r).__name__ for r in results) == ["AlreadyVoted", "dict"]
    # catches: read-then-insert on (request, session, round)
    assert (
        len(
            await fetch(
                owner_engine, "select 1 from votes where decision_request_id=:r", r=req["id"]
            )
        )
        == 1
    )


async def test_last_two_votes_concurrent_exactly_one_transition_one_receipt(
    app_engine, owner_engine
):
    w = await make_world(owner_engine, floors=LOW)
    req = await open_request(app_engine, w)
    a, b, c = await voters(owner_engine, w, 3)
    await cast(app_engine, w, req, a)
    await cast(app_engine, w, req, b)  # 2 of 3 cast: one more vote closes the round
    results = await asyncio.gather(
        cast(app_engine, w, req, c), cast(app_engine, w, req, (await voters(owner_engine, w, 1))[0]),
        return_exceptions=True,
    )  # fmt: skip
    # catches: both seeing quorum-1 and both (or neither) transitioning
    assert sorted(type(r).__name__ for r in results) == ["RoundClosed", "dict"]
    row = (
        await fetch(
            owner_engine,
            "select status, receipt is not null from decision_requests where id=:r",
            r=req["id"],
        )
    )[0]
    assert row == ("approved", True)
    assert (
        len(
            await fetch(
                owner_engine, "select 1 from votes where decision_request_id=:r", r=req["id"]
            )
        )
        == 3
    )
    changes = await fetch(
        owner_engine,
        "select 1 from audit_logs where entity_id=:r and action='status_changed'",
        r=req["id"],
    )
    assert len(changes) == 1
    revealed = await fetch(
        owner_engine,
        "select 1 from outbox_events where type='round.revealed' and payload->>'decision_request_id'=:r",
        r=str(req["id"]),
    )
    assert len(revealed) == 1


async def test_vote_after_round_closed_is_409_round_closed(app_engine, owner_engine):
    w = await make_world(owner_engine, floors=LOW)
    req = await open_request(app_engine, w)
    a, b, c, late = await voters(owner_engine, w, 4)
    for s in (a, b, c):
        await cast(app_engine, w, req, s, "reject")
    with pytest.raises(RoundClosed):  # catches: a late vote changing the tally
        await cast(app_engine, w, req, late, "approve")
    assert (
        len(
            await fetch(
                owner_engine, "select 1 from votes where decision_request_id=:r", r=req["id"]
            )
        )
        == 3
    )
    assert (
        await fetch(owner_engine, "select status from decision_requests where id=:r", r=req["id"])
    )[0][0] == "rejected"


async def test_same_session_cannot_vote_twice_even_without_distinct_agents(
    app_engine, owner_engine
):
    w = await make_world(owner_engine, distinct=False)
    req = await open_request(app_engine, w)
    (v,) = await voters(owner_engine, w, 1)
    await cast(app_engine, w, req, v)
    with pytest.raises(AlreadyVoted):
        await cast(app_engine, w, req, v)

from typing import Any

import pytest
from sqlalchemy import text

from app.db.session import tenant_session
from app.domain.errors import (
    DiscussionClosed,
    DiscussionRequired,
    Forbidden,
    InvalidInput,
    NotFoundError,
)
from app.services.votes import visible_votes

from .helpers import (
    NOW,
    add_agent,
    approval_world,
    cast,
    fetch,
    make_user,
    make_world,
    open_request,
    post,
    staff,
    voters,
)

pytestmark = pytest.mark.anyio

LOW = {"refund": "low"}


async def split_into_discussion(owner, app, **kw):
    """rounds=2 (pro plan), round 1 = approve, approve, reject -> pending_discussion."""
    w = await make_world(owner, "pro", rounds=2, **kw)
    req = await open_request(app, w)
    sessions = await voters(owner, w, 3)
    for s, v in zip(sessions, ("approve", "approve", "reject"), strict=True):
        await cast(app, w, req, s, v)
    assert (await fetch(owner, "select status from decision_requests where id=:r", r=req["id"]))[0][
        0
    ] == "pending_discussion"
    return w, req, sessions


async def test_agent_message_in_pending_votes_is_409_system_only(app_engine, owner_engine):
    w = await make_world(owner_engine)
    req = await open_request(app_engine, w)
    _, other = await add_agent(owner_engine, w)
    user = await staff(owner_engine, w.org, "member")
    whos: list[dict[str, Any]] = [{"session": w.session}, {"session": other}, {"user": user}]
    for who in whos:
        with pytest.raises(DiscussionClosed):  # catches: pre-reveal chat leaking verdicts
            await post(app_engine, w, req, "psst", **who)
    assert (
        await fetch(
            owner_engine, "select 1 from messages where decision_request_id=:r", r=req["id"]
        )
        == []
    )


async def test_messages_open_from_discussion_through_approval_for_agents_and_users(
    app_engine, owner_engine
):
    w, req, sessions = await split_into_discussion(owner_engine, app_engine)
    user = await staff(owner_engine, w.org, "member")
    await post(app_engine, w, req, "agent says", session=sessions[0])
    await post(
        app_engine, w, req, "requester says", session=w.session
    )  # the requester may post too
    await post(app_engine, w, req, "human says", user=user)
    w2, req2, _ = await approval_world(owner_engine, app_engine)  # pending_approval
    await post(app_engine, w2, req2, "still open", user=await staff(owner_engine, w2.org, "member"))
    msgs = await fetch(
        owner_engine,
        "select author_type from messages where decision_request_id=:r order by created_at, id",
        r=req["id"],
    )
    assert [m[0] for m in msgs] == ["agent", "agent", "user"]
    events = await fetch(
        owner_engine,
        "select payload::text from outbox_events where type='message.posted' and organization_id=:o",
        o=w.org,
    )
    assert len(events) == 3 and all(
        "human says" not in e[0] and "agent says" not in e[0] for e in events
    )


async def test_messages_close_again_when_the_request_is_decided(app_engine, owner_engine):
    w = await make_world(owner_engine, floors=LOW)
    req = await open_request(app_engine, w)
    for s in await voters(owner_engine, w, 3):
        await cast(app_engine, w, req, s)
    with pytest.raises(DiscussionClosed):
        await post(app_engine, w, req, "late", session=w.session)


async def test_revoked_identity_and_non_member_cannot_post(app_engine, owner_engine):
    w, req, _ = await split_into_discussion(owner_engine, app_engine)
    ident, sess = await add_agent(owner_engine, w)
    async with owner_engine.begin() as c:
        await c.execute(
            text("update agent_identities set status='revoked' where id=:i"), {"i": ident}
        )
    with pytest.raises(Forbidden):
        await post(app_engine, w, req, "x", session=sess)
    with pytest.raises(NotFoundError):
        await post(app_engine, w, req, "x", user=await make_user(owner_engine))


async def test_message_cross_tenant_request_404(app_engine, owner_engine):
    _, req_a, _ = await split_into_discussion(owner_engine, app_engine)
    b = await make_world(owner_engine)
    with pytest.raises(NotFoundError):  # catches: posting to another tenant's thread
        await post(app_engine, b, req_a, "x", user=await staff(owner_engine, b.org, "member"))


@pytest.mark.parametrize("body", ["", "a\x00b", "\ud800", "x" * 4097, None, 5])
async def test_invalid_message_body_is_rejected(app_engine, owner_engine, body):
    w, req, sessions = await split_into_discussion(owner_engine, app_engine)
    with pytest.raises(InvalidInput):
        await post(app_engine, w, req, body, session=sessions[0])


async def test_round_two_vote_needs_a_message(app_engine, owner_engine):
    w, req, sessions = await split_into_discussion(owner_engine, app_engine)
    with pytest.raises(DiscussionRequired):  # catches: re-vote with no discussion
        await cast(app_engine, w, req, sessions[0], "reject")
    await post(app_engine, w, req, "let us discuss", session=sessions[1])
    assert (await cast(app_engine, w, req, sessions[0], "reject"))["round"] == 2


async def test_round_two_decides_on_round_two_votes_only_and_is_not_blind(app_engine, owner_engine):
    w, req, sessions = await split_into_discussion(owner_engine, app_engine, floors=LOW)
    await post(app_engine, w, req, "discussing", user=await staff(owner_engine, w.org, "member"))
    for s in sessions:
        await cast(app_engine, w, req, s, "approve")  # round 1 had a reject; round 2 is unanimous
    status = (
        await fetch(
            owner_engine,
            "select status, receipt is not null from decision_requests where id=:r",
            r=req["id"],
        )
    )[0]
    assert status == ("approved", True)  # catches: stale round-1 votes mixed in
    async with tenant_session(app_engine, w.org) as s:
        seen = await visible_votes(s, w.org, req["id"], viewer_session_id=None)
    assert len([v for v in seen["votes"] if v["round"] == 2]) == 3
    events = await fetch(
        owner_engine,
        "select payload from outbox_events where type='vote.cast' and organization_id=:o and payload->>'round'='2'",
        o=w.org,
    )
    assert len(events) == 3 and all(
        e[0]["verdict"] == "approve" for e in events
    )  # round 2 is open, not blind


async def test_round_two_unanimous_reject_and_medium_risk_paths(app_engine, owner_engine):
    w, req, sessions = await split_into_discussion(
        owner_engine, app_engine
    )  # medium: unknown action type
    await post(app_engine, w, req, "x", session=sessions[0])
    for s in sessions:
        await cast(app_engine, w, req, s, "approve")
    assert (
        await fetch(owner_engine, "select status from decision_requests where id=:r", r=req["id"])
    )[0][0] == "pending_approval"
    assert (
        len(
            await fetch(
                owner_engine,
                "select 1 from approval_steps where decision_request_id=:r and status='pending'",
                r=req["id"],
            )
        )
        == 1
    )
    w2, req2, s2 = await split_into_discussion(owner_engine, app_engine)
    await post(app_engine, w2, req2, "x", session=s2[0])
    for s in s2:
        await cast(app_engine, w2, req2, s, "reject")
    assert (
        await fetch(owner_engine, "select status from decision_requests where id=:r", r=req2["id"])
    )[0][0] == "rejected"


async def test_entering_discussion_restarts_the_timer(app_engine, owner_engine):
    from datetime import timedelta

    from .helpers import FakeClock

    w = await make_world(owner_engine, "pro", rounds=2, timeout=30)
    req = await open_request(app_engine, w)
    at = FakeClock(NOW.now() + timedelta(minutes=10))
    for s, v in zip(
        await voters(owner_engine, w, 3), ("approve", "approve", "reject"), strict=True
    ):
        await cast(app_engine, w, req, s, v, clock=at)
    deadline = (
        await fetch(
            owner_engine,
            "select voting_deadline_at from decision_requests where id=:r",
            r=req["id"],
        )
    )[0][0]
    assert deadline == at.now() + timedelta(
        minutes=30
    )  # the timer runs again from entry to pending_discussion

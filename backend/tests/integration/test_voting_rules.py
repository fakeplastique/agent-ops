import pytest
from sqlalchemy import text

from app.domain.errors import (
    AlreadyVoted,
    InvalidInput,
    NotFoundError,
    RoundClosed,
    StaleVersion,
    VoteNotAllowed,
)
from app.domain.receipt import Subject, verify
from app.domain.statuses import Status

from .helpers import KEYS, add_agent, cast, fetch, make_world, open_request, voters

pytestmark = pytest.mark.anyio

LOW = {"refund": "low"}


async def row(owner, req):
    return (
        await fetch(
            owner,
            "select status, receipt, decided_at, version, content_hash from decision_requests where id=:r",
            r=req["id"],
        )
    )[0]


async def test_requester_identity_other_session_cannot_vote(app_engine, owner_engine):
    w = await make_world(owner_engine)
    req = await open_request(app_engine, w)
    async with owner_engine.begin() as c:  # a second session of the REQUESTER's identity
        await c.execute(
            text(
                "insert into agent_sessions (id, organization_id, agent_identity_id, session_label) values (gen_random_uuid(), :o, :i, 'second')"
            ),
            {"o": w.org, "i": w.identity},
        )
    second = (
        await fetch(
            owner_engine,
            "select id from agent_sessions where agent_identity_id=:i and session_label='second'",
            i=w.identity,
        )
    )[0][0]
    for s in (w.session, second):
        with pytest.raises(VoteNotAllowed):  # catches: checking session id instead of identity
            await cast(app_engine, w, req, s)
    assert await fetch(owner_engine, "select 1 from votes where organization_id=:o", o=w.org) == []


async def test_revoked_or_inactive_identity_vote_rejected_mid_round(app_engine, owner_engine):
    w = await make_world(owner_engine)
    req = await open_request(app_engine, w)
    ident, sess = await add_agent(owner_engine, w)
    await cast(app_engine, w, req, (await voters(owner_engine, w, 1))[0])
    async with owner_engine.begin() as c:
        await c.execute(
            text("update agent_identities set status='revoked' where id=:i"), {"i": ident}
        )
    with pytest.raises(VoteNotAllowed):  # catches: status checked only at session creation
        await cast(app_engine, w, req, sess)


async def test_distinct_agents_one_vote_per_identity_per_round(app_engine, owner_engine):
    w = await make_world(owner_engine, distinct=True)
    req = await open_request(app_engine, w)
    ident, first = await add_agent(owner_engine, w)
    async with owner_engine.begin() as c:
        await c.execute(
            text(
                "insert into agent_sessions (id, organization_id, agent_identity_id, session_label) values (gen_random_uuid(), :o, :i, 'again')"
            ),
            {"o": w.org, "i": ident},
        )
    again = (
        await fetch(
            owner_engine,
            "select id from agent_sessions where agent_identity_id=:i and session_label='again'",
            i=ident,
        )
    )[0][0]
    await cast(app_engine, w, req, first)
    with pytest.raises(AlreadyVoted):  # catches: sessions of one identity filling the quorum
        await cast(app_engine, w, req, again)


async def test_without_distinct_agents_two_sessions_of_one_identity_both_count(
    app_engine, owner_engine
):
    w = await make_world(owner_engine, distinct=False)
    req = await open_request(app_engine, w)
    ident, first = await add_agent(owner_engine, w)
    async with owner_engine.begin() as c:
        await c.execute(
            text(
                "insert into agent_sessions (id, organization_id, agent_identity_id, session_label) values (gen_random_uuid(), :o, :i, 'again')"
            ),
            {"o": w.org, "i": ident},
        )
    again = (
        await fetch(
            owner_engine,
            "select id from agent_sessions where agent_identity_id=:i and session_label='again'",
            i=ident,
        )
    )[0][0]
    await cast(app_engine, w, req, first)
    await cast(app_engine, w, req, again)
    assert (
        len(await fetch(owner_engine, "select 1 from votes where organization_id=:o", o=w.org)) == 2
    )


async def test_vote_on_stale_version_409(app_engine, owner_engine):
    w = await make_world(owner_engine)
    req = await open_request(app_engine, w)
    (v,) = await voters(owner_engine, w, 1)
    async with owner_engine.begin() as c:  # what S11's revise will do
        await c.execute(
            text("update decision_requests set version = 2 where id=:r"), {"r": req["id"]}
        )
    with pytest.raises(StaleVersion):  # catches: a vote for v1 counted after revise
        await cast(app_engine, w, req, v, request_version=1)
    await cast(app_engine, w, req, v, request_version=2)


async def test_auto_approve_issues_receipt_with_auto_approvers(app_engine, owner_engine):
    w = await make_world(owner_engine, floors=LOW)
    req = await open_request(app_engine, w)
    a, b, c = await voters(owner_engine, w, 3)
    for s in (a, b):
        assert (await cast(app_engine, w, req, s))["status"] == "pending_votes"
    out = await cast(app_engine, w, req, c)
    assert out["status"] == "approved"
    status, receipt, decided_at, version, digest = await row(owner_engine, req)
    # catches: an auto-approved request without a receipt, or one that does not verify
    live = Subject(req["id"], version, digest, decided_at, "auto")
    assert receipt["payload"]["approvers"] == "auto"
    assert verify(receipt, live, Status(status), KEYS)
    assert not verify(
        receipt, Subject(req["id"], version, "0" * 64, decided_at, "auto"), Status(status), KEYS
    )


@pytest.mark.parametrize(
    ("verdicts", "confidence", "floors", "final"),
    [
        (["reject"] * 3, 0.9, LOW, "rejected"),  # S3: all reject, no humans, no receipt
        (["approve", "approve", "reject"], 0.9, LOW, "pending_approval"),  # split, rounds=1
        (["approve", "approve", "abstain"], 0.9, LOW, "pending_approval"),  # abstain is not approve
        (["approve"] * 3, 0.79, LOW, "pending_approval"),  # below the 0.8 threshold
        (["approve"] * 3, 0.8, LOW, "approved"),  # equal to the threshold approves
        (
            ["approve"] * 3,
            0.9,
            {},
            "pending_approval",
        ),  # unknown action type floors at medium (SC4)
        (["approve"] * 3, 0.9, {"refund": "high"}, "pending_approval"),
        (["abstain"] * 3, 0.9, LOW, "pending_approval"),
    ],
)
async def test_round_outcomes_go_through_the_one_rule(
    app_engine, owner_engine, verdicts, confidence, floors, final
):
    w = await make_world(owner_engine, floors=floors)
    req = await open_request(app_engine, w)
    for s, v in zip(await voters(owner_engine, w, 3), verdicts, strict=True):
        await cast(app_engine, w, req, s, v, confidence)
    status, receipt, decided_at, *_ = await row(owner_engine, req)
    assert status == final
    assert (receipt is not None) == (final == "approved")
    assert (decided_at is not None) == (final in ("approved", "rejected"))


async def test_split_with_two_rounds_goes_to_discussion_and_votes_are_closed_until_s11(
    app_engine, owner_engine
):
    w = await make_world(owner_engine, rounds=2, floors=LOW)
    async with owner_engine.begin() as c:
        await c.execute(text("update organizations set plan_key='pro' where id=:o"), {"o": w.org})
    req = await open_request(app_engine, w)
    a, b, c_, late = await voters(owner_engine, w, 4)
    for s, v in ((a, "approve"), (b, "approve"), (c_, "reject")):
        await cast(app_engine, w, req, s, v)
    assert (await row(owner_engine, req))[0] == "pending_discussion"
    with pytest.raises(RoundClosed):  # round 2 opens with S11's discussion gate
        await cast(app_engine, w, req, late)


async def test_vote_while_awaiting_humans_is_round_closed(app_engine, owner_engine):
    w = await make_world(owner_engine)
    req = await open_request(app_engine, w)
    a, b, c, late = await voters(owner_engine, w, 4)
    for s in (a, b, c):
        await cast(app_engine, w, req, s)
    assert (await row(owner_engine, req))[0] == "pending_approval"
    with pytest.raises(RoundClosed):
        await cast(app_engine, w, req, late)


@pytest.mark.parametrize(
    "bad",
    [
        {"verdict": "maybe"},
        {"confidence": float("nan")},
        {"confidence": 1.5},
        {"confidence": True},
        {"rationale": "x" * 4097},
        {"rationale": "a\x00b"},
        {"rationale": "\ud800"},
        {"request_version": 0},
        {"request_version": True},
    ],
)
async def test_invalid_vote_input_is_rejected_and_written_nowhere(app_engine, owner_engine, bad):
    w = await make_world(owner_engine)
    req = await open_request(app_engine, w)
    (v,) = await voters(owner_engine, w, 1)
    with pytest.raises((InvalidInput, StaleVersion)):
        await cast(app_engine, w, req, v, **bad)
    assert await fetch(owner_engine, "select 1 from votes where organization_id=:o", o=w.org) == []
    assert (
        await fetch(
            owner_engine,
            "select 1 from audit_logs where entity_type='vote' and organization_id=:o",
            o=w.org,
        )
        == []
    )


async def test_other_tenants_request_and_session_are_not_found(app_engine, owner_engine):
    a, b = await make_world(owner_engine), await make_world(owner_engine)
    req_a = await open_request(app_engine, a)
    (voter_b,) = await voters(owner_engine, b, 1)
    with pytest.raises(NotFoundError):  # tenant B voting on tenant A's request
        await cast(app_engine, b, req_a, voter_b)
    (voter_a,) = await voters(owner_engine, a, 1)
    with pytest.raises(NotFoundError):  # tenant A's session used from tenant B's context
        await cast(app_engine, b, await open_request(app_engine, b), voter_a)
    assert (
        await fetch(
            owner_engine, "select 1 from votes where organization_id in (:a, :b)", a=a.org, b=b.org
        )
        == []
    )

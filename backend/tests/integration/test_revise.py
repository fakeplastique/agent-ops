import asyncio
from datetime import timedelta
from typing import Any

import pytest
from sqlalchemy import text

from app.db.session import tenant_session
from app.domain.errors import (
    Forbidden,
    IllegalTransitionError,
    InvalidInput,
    NotFoundError,
    PolicyExceedsPlan,
    StaleVersion,
)
from app.domain.hashing import content_hash
from app.domain.statuses import Risk
from app.services.votes import visible_votes

from .helpers import (
    ARGS,
    NOW,
    FakeClock,
    add_agent,
    approval_world,
    cast,
    fetch,
    make_world,
    open_request,
    pending_step,
    revise,
    staff,
    voters,
    withdraw,
)  # fmt: skip

pytestmark = pytest.mark.anyio

LOW = {"refund": "low"}


async def current(owner, req):
    return (
        await fetch(
            owner,
            "select status, version, content_hash, voting_deadline_at, policy_snapshot, title from decision_requests where id=:r",
            r=req["id"],
        )
    )[0]


async def test_revise_while_pending_approval_supersedes_steps_and_excludes_old_votes(
    app_engine, owner_engine
):
    w, req, _ = await approval_world(
        owner_engine, app_engine, levels=[{"role": "manager", "timeout_minutes": 60}]
    )
    step, *_ = await pending_step(owner_engine, req)
    out = await revise(app_engine, w, req, title="Refund order 8")
    status, version, digest, *_ = await current(owner_engine, req)
    assert (out["version"], version, status) == (2, 2, "pending_votes")
    assert (
        digest
        == content_hash("Refund order 8", "refund", ARGS["payload"], Risk.LOW)
        != req["content_hash"]
    )
    assert (await fetch(owner_engine, "select status from approval_steps where id=:s", s=step))[0][
        0
    ] == "superseded"
    async with tenant_session(
        app_engine, w.org
    ) as s:  # catches: old votes still counting after a change
        seen = await visible_votes(s, w.org, req["id"], viewer_session_id=None)
    assert seen["cast"] == 0 and seen["version"] == 2
    assert (
        len(
            await fetch(
                owner_engine,
                "select 1 from votes where decision_request_id=:r and request_version=1",
                r=req["id"],
            )
        )
        == 3
    )
    msgs = await fetch(
        owner_engine,
        "select author_type, body, request_version from messages where decision_request_id=:r",
        r=req["id"],
    )
    assert len(msgs) == 1 and msgs[0][0] == "system" and "title" in msgs[0][1] and msgs[0][2] == 2
    kinds = [
        r[0]
        for r in await fetch(
            owner_engine,
            "select action from audit_logs where entity_id=:r order by created_at, id",
            r=req["id"],
        )
    ]
    assert (
        kinds.count("revised") == 1 and kinds.count("status_changed") == 2
    )  # close, then the revise back to voting


async def test_a_vote_for_the_old_version_is_stale_after_revise_and_the_new_round_starts_empty(
    app_engine, owner_engine
):
    w = await make_world(owner_engine, floors=LOW)
    req = await open_request(app_engine, w)
    a, b, c, d = await voters(owner_engine, w, 4)
    await cast(app_engine, w, req, a)
    await revise(app_engine, w, req, description="changed")
    with pytest.raises(StaleVersion):
        await cast(app_engine, w, req, b, request_version=1)
    v2 = {**req, "version": 2}
    for s in (a, b, c):  # the same session may vote again on the new version
        await cast(app_engine, w, v2, s)
    assert (await current(owner_engine, req))[0] == "approved"
    assert d  # unused fourth voter keeps the fixture honest


async def test_revise_races_with_last_vote_one_consistent_outcome(app_engine, owner_engine):
    w = await make_world(owner_engine, floors=LOW)
    req = await open_request(app_engine, w)
    a, b, c = await voters(owner_engine, w, 3)
    await cast(app_engine, w, req, a)
    await cast(app_engine, w, req, b)
    vote_res, revise_res = await asyncio.gather(
        cast(app_engine, w, req, c),
        revise(app_engine, w, req, title="raced"),
        return_exceptions=True,
    )
    status, version, *_ = await current(owner_engine, req)
    # catches: a vote for v1 landing after the bump (or a revise of an approved request)
    if isinstance(
        vote_res, dict
    ):  # the vote closed the round first: approved, so the revise is refused
        assert isinstance(revise_res, IllegalTransitionError) and (status, version) == (
            "approved",
            1,
        )
    else:  # the revise won: the late vote is stale and nothing closed
        assert isinstance(vote_res, StaleVersion) and (status, version) == ("pending_votes", 2)


async def test_revise_by_other_agent_identity_denied_same_identity_new_session_allowed(
    app_engine, owner_engine
):
    w = await make_world(owner_engine)
    req = await open_request(app_engine, w)
    _, other_session = await add_agent(owner_engine, w)
    with pytest.raises(Forbidden):  # catches: ownership by session instead of identity
        await revise(app_engine, w, req, session=other_session, title="not mine")
    async with owner_engine.begin() as c:  # a second session of the ORIGINAL identity
        await c.execute(
            text(
                "insert into agent_sessions (id, organization_id, agent_identity_id, session_label) values (gen_random_uuid(), :o, :i, 'new')"
            ),
            {"o": w.org, "i": w.identity},
        )
    new = (
        await fetch(
            owner_engine,
            "select id from agent_sessions where agent_identity_id=:i and session_label='new'",
            i=w.identity,
        )
    )[0][0]
    assert (await revise(app_engine, w, req, session=new, title="mine"))["version"] == 2


async def test_a_request_made_by_a_user_cannot_be_revised_by_an_agent(app_engine, owner_engine):
    from app.services.decisions import create_decision

    w = await make_world(owner_engine)
    user = await staff(owner_engine, w.org, "member")
    async with tenant_session(app_engine, w.org) as s:
        made = await create_decision(s, w.org, NOW, user_id=user, **ARGS)
    with pytest.raises(Forbidden):
        await revise(app_engine, w, made, title="x")


async def test_revise_resets_deadline_and_re_resolves_policy(app_engine, owner_engine):
    w = await make_world(owner_engine, quorum=3, timeout=60)
    req = await open_request(app_engine, w)
    async with owner_engine.begin() as c:
        await c.execute(
            text(
                "update escalation_policies set quorum_size=2, voting_timeout_minutes=15 where id=:p"
            ),
            {"p": w.policy},
        )
    later = FakeClock(NOW.now() + timedelta(minutes=45))
    await revise(app_engine, w, req, clock=later, title="again")
    _, version, _, deadline, snapshot, _ = await current(owner_engine, req)
    # catches: a stale snapshot or an expired timer
    assert (
        snapshot["quorum_size"] == 2
        and deadline == later.now() + timedelta(minutes=15)
        and version == 2
    )


async def test_diff_message_names_fields_with_capped_values(app_engine, owner_engine):
    w = await make_world(owner_engine)
    req = await open_request(app_engine, w)
    big = {"blob": "x" * 60_000}
    await revise(app_engine, w, req, title="T" * 190, payload=big, risk_level="high")
    body = (
        await fetch(
            owner_engine, "select body from messages where decision_request_id=:r", r=req["id"]
        )
    )[0][0]
    # catches: a 64 KB payload copied into the thread
    assert (
        len(body) < 800
        and "title" in body
        and "payload" in body
        and "risk_level" in body
        and "xxxxxxxxxx" not in body.replace("T", "")
    )


async def test_revise_needs_a_real_change_and_valid_input(app_engine, owner_engine):
    w = await make_world(owner_engine)
    req = await open_request(app_engine, w)
    bads: list[dict[str, Any]] = [
        {},
        {"title": ARGS["title"]},
        {"title": ""},
        {"title": "a\x00b"},
        {"payload": ["x"]},
        {"risk_level": "critical"},
        {"action_type": "Bad Type"},
        {"description": "\ud800"},
    ]
    for bad in bads:
        with pytest.raises(InvalidInput):
            await revise(app_engine, w, req, **bad)
    assert (await current(owner_engine, req))[1] == 1


async def test_revise_does_not_count_against_the_plan_and_respects_it(app_engine, owner_engine):
    w = await make_world(owner_engine, floors=LOW)
    req = await open_request(app_engine, w)
    used = lambda: fetch(
        owner_engine, "select value from usage_counters where organization_id=:o", o=w.org
    )
    before = await used()
    await revise(app_engine, w, req, title="r1")
    assert await used() == before  # revisions do not count
    async with owner_engine.begin() as c:  # the policy now exceeds the plan (a downgrade)
        await c.execute(
            text("update escalation_policies set quorum_size=9 where id=:p"), {"p": w.policy}
        )
    with pytest.raises(PolicyExceedsPlan):
        await revise(app_engine, w, req, title="r2")
    assert (await current(owner_engine, req))[1] == 2


async def test_revise_of_a_decided_or_foreign_request_is_refused(app_engine, owner_engine):
    w = await make_world(owner_engine, floors=LOW)
    req = await open_request(app_engine, w)
    for s in await voters(owner_engine, w, 3):
        await cast(app_engine, w, req, s)
    with pytest.raises(IllegalTransitionError):
        await revise(app_engine, w, req, title="too late")
    other = await make_world(owner_engine)
    with pytest.raises(NotFoundError):
        async with tenant_session(app_engine, other.org) as s:
            from app.services.decisions import revise_decision

            await revise_decision(
                s,
                other.org,
                NOW,
                decision_request_id=req["id"],
                agent_session_id=other.session,
                title="x",
            )


async def test_withdraw_by_org_admin_or_original_identity_only(app_engine, owner_engine):
    w = await make_world(owner_engine)
    for who in ("member", "manager", "team_lead"):
        req = await open_request(app_engine, w)
        with pytest.raises(Forbidden):  # catches: any member cancelling
            await withdraw(app_engine, w, req, user=await staff(owner_engine, w.org, who))
    req = await open_request(app_engine, w)
    _, other_session = await add_agent(owner_engine, w)
    with pytest.raises(Forbidden):
        await withdraw(app_engine, w, req, session=other_session)
    assert (await withdraw(app_engine, w, req, session=w.session))["status"] == "cancelled"
    req2 = await open_request(app_engine, w)
    assert (
        await withdraw(app_engine, w, req2, user=await staff(owner_engine, w.org, "org_admin"))
    )["status"] == "cancelled"


async def test_withdraw_terminal_is_409_illegal_transition(app_engine, owner_engine):
    w = await make_world(owner_engine, floors=LOW)
    req = await open_request(app_engine, w)
    for s in await voters(owner_engine, w, 3):
        await cast(app_engine, w, req, s)
    with pytest.raises(IllegalTransitionError):  # catches: cancelling an approved request
        await withdraw(app_engine, w, req, session=w.session)
    again = await open_request(app_engine, w)
    await withdraw(app_engine, w, again, session=w.session)
    with pytest.raises(IllegalTransitionError):
        await withdraw(app_engine, w, again, session=w.session)


async def test_withdraw_supersedes_pending_steps_and_keeps_blind_votes_blind(
    app_engine, owner_engine
):
    w, req, _ = await approval_world(
        owner_engine, app_engine, levels=[{"role": "manager", "timeout_minutes": 60}]
    )
    step, *_ = await pending_step(owner_engine, req)
    await withdraw(app_engine, w, req, session=w.session)
    assert (await fetch(owner_engine, "select status from approval_steps where id=:s", s=step))[0][
        0
    ] == "superseded"
    w2 = await make_world(owner_engine)
    mid = await open_request(app_engine, w2)
    a, *_ = await voters(owner_engine, w2, 3)
    await cast(app_engine, w2, mid, a, "reject")
    await withdraw(app_engine, w2, mid, session=w2.session)
    async with tenant_session(app_engine, w2.org) as s:
        out = await visible_votes(s, w2.org, mid["id"], viewer_session_id=None)
    assert out["votes"] == [] and out["status"] == "cancelled"  # withdrawing never unmasks round 1


async def test_revise_compares_the_payload_the_way_the_hash_does(app_engine, owner_engine):
    w = await make_world(owner_engine)
    req = await open_request(app_engine, w, payload={"a": 1, "b": 2})
    with pytest.raises(InvalidInput):  # 1 and 1.0 hash alike: nothing changed
        await revise(app_engine, w, req, payload={"a": 1.0, "b": 2})
    out = await revise(app_engine, w, req, payload={"a": True, "b": 2})  # True is not 1 to the hash
    assert out["fields"] == ["payload"]
    out2 = await revise(
        app_engine, w, {**req, "version": 2}, title="new title", payload={"a": 1, "b": 2}
    )
    assert out2["fields"] == [
        "title",
        "payload",
    ]  # catches: a rewritten payload missing from the diff

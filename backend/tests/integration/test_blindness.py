import json

import pytest
from sqlalchemy import text

from app.db.session import tenant_session
from app.services.votes import visible_votes

from .helpers import cast, fetch, make_world, open_request, voters

pytestmark = pytest.mark.anyio

SECRET = "rationale-SECRET-xyz"


async def view(engine, w, req, viewer):
    async with tenant_session(engine, w.org) as s:
        return await visible_votes(s, w.org, req["id"], viewer_session_id=viewer)


async def test_before_own_vote_no_other_verdict_confidence_or_rationale_returned(
    app_engine, owner_engine
):
    w = await make_world(owner_engine, floors={"refund": "low"})
    req = await open_request(app_engine, w)
    a, b, _ = await voters(owner_engine, w, 3)
    await cast(app_engine, w, req, a, "reject", 0.123, rationale=SECRET)
    for viewer in (b, None, w.session):  # a reviewer who has not voted, a human, the requester
        out = await view(app_engine, w, req, viewer)
        blob = json.dumps(out, default=str)
        # catches: a read path forgetting a field
        assert (
            out["votes"] == []
            and SECRET not in blob
            and "reject" not in blob
            and "0.123" not in blob
        )
    mine = await view(app_engine, w, req, a)  # a voter sees only their own vote while blind
    assert [v["session_id"] for v in mine["votes"]] == [a] and mine["votes"][0][
        "rationale"
    ] == SECRET
    await cast(app_engine, w, req, b, "approve")
    other = await view(app_engine, w, req, b)
    assert [v["session_id"] for v in other["votes"]] == [b]  # still blind to a's vote


async def test_vote_counts_visible_while_blind(app_engine, owner_engine):
    w = await make_world(owner_engine)
    req = await open_request(app_engine, w)
    a, b, _ = await voters(owner_engine, w, 3)
    await cast(app_engine, w, req, a)
    await cast(app_engine, w, req, b)
    out = await view(app_engine, w, req, None)
    assert (out["cast"], out["quorum"]) == (2, 3)  # catches: over-blinding that hides "2 of 3 cast"


async def test_all_votes_visible_once_the_round_is_revealed(app_engine, owner_engine):
    w = await make_world(owner_engine)  # medium floor: unanimous approve still goes to humans
    req = await open_request(app_engine, w)
    a, b, c = await voters(owner_engine, w, 3)
    for s, verdict in ((a, "approve"), (b, "approve"), (c, "reject")):
        await cast(app_engine, w, req, s, verdict, rationale=f"why-{verdict}")
    out = await view(app_engine, w, req, None)
    assert sorted(v["verdict"] for v in out["votes"]) == ["approve", "approve", "reject"]
    assert out["status"] == "pending_approval"


async def test_audit_vote_cast_and_outbox_payload_omit_verdict_until_round_revealed(
    app_engine, owner_engine
):
    w = await make_world(owner_engine)
    req = await open_request(app_engine, w)
    a, b, _ = await voters(owner_engine, w, 3)
    await cast(app_engine, w, req, a, "reject", 0.4242, rationale=SECRET)
    await cast(app_engine, w, req, b, "approve", 0.5151, rationale=SECRET)
    audit = await fetch(
        owner_engine,
        "select action, data::text from audit_logs where entity_type='vote' and organization_id=:o",
        o=w.org,
    )
    events = await fetch(
        owner_engine,
        "select type, payload::text from outbox_events where type='vote.cast' and organization_id=:o",
        o=w.org,
    )
    assert len(audit) == 2 and len(events) == 2
    for blob in [d for _, d in audit] + [p for _, p in events]:
        # catches: blind data leaking through events and audit
        assert all(x not in blob for x in ("reject", "approve", "0.4242", "0.5151", SECRET)), blob
        assert "session_id" in blob


async def test_round_revealed_event_carries_votes_once(app_engine, owner_engine):
    w = await make_world(owner_engine)
    req = await open_request(app_engine, w)
    a, b, c = await voters(owner_engine, w, 3)
    for s in (a, b):
        await cast(app_engine, w, req, s, "approve")
    assert (
        await fetch(
            owner_engine,
            "select 1 from outbox_events where type='round.revealed' and organization_id=:o",
            o=w.org,
        )
        == []
    )
    await cast(app_engine, w, req, c, "approve")
    ev = await fetch(
        owner_engine,
        "select payload from outbox_events where type='round.revealed' and organization_id=:o",
        o=w.org,
    )
    assert len(ev) == 1  # catches: reveal emitted per vote or never
    votes = ev[0][0]["votes"]
    assert sorted(v["verdict"] for v in votes) == ["approve"] * 3 and all(
        "rationale" not in v for v in votes
    )
    audit = await fetch(
        owner_engine,
        "select data from audit_logs where action='round_revealed' and organization_id=:o",
        o=w.org,
    )
    assert len(audit) == 1 and len(audit[0][0]["votes"]) == 3


async def test_rationale_never_appears_in_any_event_or_audit_row(app_engine, owner_engine):
    w = await make_world(owner_engine, floors={"refund": "low"})
    req = await open_request(app_engine, w)
    for s in await voters(owner_engine, w, 3):
        await cast(app_engine, w, req, s, "approve", rationale=SECRET)
    for table, col in (("audit_logs", "data"), ("outbox_events", "payload")):
        rows = await fetch(
            owner_engine, f"select {col}::text from {table} where organization_id=:o", o=w.org
        )
        assert all(SECRET not in r[0] for r in rows), table


@pytest.mark.parametrize("status", ["cancelled", "expired"])
async def test_leaving_pending_votes_without_closing_the_round_reveals_nothing(
    app_engine, owner_engine, status
):
    # catches: withdrawing mid-round unmasking the blind votes (the requester could read them)
    w = await make_world(owner_engine)
    req = await open_request(app_engine, w)
    a, b, _ = await voters(owner_engine, w, 3)
    await cast(app_engine, w, req, a, "reject", 0.31, rationale=SECRET)
    async with owner_engine.begin() as c:
        await c.execute(text("alter table decision_requests disable trigger decision_status_guard"))
        await c.execute(
            text("update decision_requests set status=:s where id=:r"),
            {"s": status, "r": req["id"]},
        )
        await c.execute(text("alter table decision_requests enable trigger decision_status_guard"))
    for viewer in (b, None, w.session):
        out = await view(app_engine, w, req, viewer)
        assert out["votes"] == [] and out["cast"] == 1, (status, viewer)
    assert [v["session_id"] for v in (await view(app_engine, w, req, a))["votes"]] == [a]


async def test_closing_the_round_stamps_every_round_one_vote_revealed(app_engine, owner_engine):
    w = await make_world(owner_engine)
    req = await open_request(app_engine, w)
    a, b, c = await voters(owner_engine, w, 3)
    for s in (a, b):
        await cast(app_engine, w, req, s)
    q = "select count(revealed_at) from votes where organization_id=:o"
    assert (await fetch(owner_engine, q, o=w.org))[0][0] == 0
    await cast(app_engine, w, req, c)
    assert (await fetch(owner_engine, q, o=w.org))[0][0] == 3

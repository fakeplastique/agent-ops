import pytest
from sqlalchemy import text

from app.authz.principals import AgentPrincipal
from app.db.session import tenant_session
from app.domain.errors import AlreadyDecided, Forbidden, NotFoundError, StaleVersion, StepNotPending
from app.domain.receipt import Subject, verify
from app.domain.statuses import Status
from app.services.steps import decide_step

from .helpers import (
    KEYS,
    NOW,
    approval_world,
    decide,
    fetch,
    make_team,
    make_user,
    make_world,
    pending_step,
    staff,
)

pytestmark = pytest.mark.anyio


async def request_row(owner, req):
    return (
        await fetch(
            owner,
            "select status, receipt, decided_at, version, content_hash from decision_requests where id=:r",
            r=req["id"],
        )
    )[0]


async def test_pending_approval_creates_exactly_the_first_step(app_engine, owner_engine):
    w, req, _ = await approval_world(owner_engine, app_engine)
    step, level, role = await pending_step(owner_engine, req)
    assert (level, role) == (1, "team_lead")
    due, version = (
        await fetch(
            owner_engine, "select due_at, request_version from approval_steps where id=:s", s=step
        )
    )[0]
    assert due == NOW.now() + __import__("datetime").timedelta(minutes=30) and version == 1
    assert (
        len(
            await fetch(
                owner_engine,
                "select 1 from approval_steps where decision_request_id=:r",
                r=req["id"],
            )
        )
        == 1
    )
    created = await fetch(
        owner_engine,
        "select 1 from outbox_events where type='step.created' and organization_id=:o",
        o=w.org,
    )
    assert len(created) == 1


async def test_medium_approved_after_level_one_high_after_all(app_engine, owner_engine):
    # medium: level 1 only (the policy has three levels)
    w, req, team = await approval_world(owner_engine, app_engine)
    lead = await staff(owner_engine, w.org, "team_lead", team)
    step, *_ = await pending_step(owner_engine, req)
    out = await decide(app_engine, w, step, lead, "team_lead")
    assert out["request_status"] == "approved"  # catches: medium walking all levels
    status, receipt, decided_at, version, digest = await request_row(owner_engine, req)
    assert receipt["payload"]["approvers"] == [str(lead)]
    assert verify(
        receipt, Subject(req["id"], version, digest, decided_at, (lead,)), Status(status), KEYS
    )

    # high: all three levels, one pending at a time, in order
    w, req, team = await approval_world(owner_engine, app_engine, high=True)
    people = [
        await staff(owner_engine, w.org, "team_lead", team),
        await staff(owner_engine, w.org, "manager"),
        await staff(owner_engine, w.org, "org_admin"),
    ]
    for expected_level, (who, role) in enumerate(
        zip(people, ("team_lead", "manager", "org_admin"), strict=True), 1
    ):
        step, level, _ = await pending_step(owner_engine, req)  # exactly one pending step
        assert level == expected_level
        out = await decide(app_engine, w, step, who, role)
        assert out["request_status"] == ("approved" if expected_level == 3 else "pending_approval")
    status, receipt, decided_at, version, digest = await request_row(owner_engine, req)
    assert status == "approved" and sorted(receipt["payload"]["approvers"]) == sorted(
        str(p) for p in people
    )
    assert verify(
        receipt,
        Subject(req["id"], version, digest, decided_at, tuple(people)),
        Status(status),
        KEYS,
    )


@pytest.mark.parametrize("reject_at", [1, 2, 3])
async def test_reject_at_any_level_rejects_and_issues_no_receipt(
    app_engine, owner_engine, reject_at
):
    w, req, team = await approval_world(owner_engine, app_engine, high=True)
    people = [
        await staff(owner_engine, w.org, "team_lead", team),
        await staff(owner_engine, w.org, "manager"),
        await staff(owner_engine, w.org, "org_admin"),
    ]
    roles = ("team_lead", "manager", "org_admin")
    for level in range(1, reject_at):
        step, *_ = await pending_step(owner_engine, req)
        await decide(app_engine, w, step, people[level - 1], roles[level - 1])
    step, *_ = await pending_step(owner_engine, req)
    out = await decide(
        app_engine,
        w,
        step,
        people[reject_at - 1],
        roles[reject_at - 1],
        approve=False,
        comment="no",
    )
    assert out["request_status"] == "rejected"  # catches: a receipt on a rejection
    status, receipt, decided_at, *_ = await request_row(owner_engine, req)
    assert (status, receipt) == ("rejected", None) and decided_at is not None
    assert (
        await fetch(
            owner_engine,
            "select 1 from approval_steps where decision_request_id=:r and status='pending'",
            r=req["id"],
        )
        == []
    )


async def test_member_cannot_decide_team_lead_step(app_engine, owner_engine):
    w, req, team = await approval_world(owner_engine, app_engine)
    step, *_ = await pending_step(owner_engine, req)
    member = await staff(owner_engine, w.org, "member", team)
    with pytest.raises(Forbidden):  # catches: role comparison inverted
        await decide(app_engine, w, step, member, "org_admin")  # the claimed role is ignored
    assert (await pending_step(owner_engine, req))[0] == step


async def test_team_lead_of_another_team_is_denied_ancestor_lead_allowed(app_engine, owner_engine):
    w, req, team = await approval_world(owner_engine, app_engine)
    step, *_ = await pending_step(owner_engine, req)
    sibling = await make_team(owner_engine, w.org)
    stranger_lead = await staff(owner_engine, w.org, "team_lead", sibling)
    with pytest.raises(Forbidden):
        await decide(app_engine, w, step, stranger_lead, "team_lead")
    parent = await make_team(owner_engine, w.org)
    async with owner_engine.begin() as c:  # the requester's team now sits under `parent`
        await c.execute(
            text("update teams set parent_team_id=:p where id=:t"), {"p": parent, "t": team}
        )
    boss = await staff(owner_engine, w.org, "team_lead", parent)
    assert (await decide(app_engine, w, step, boss, "team_lead"))["status"] == "approved"


async def test_demoted_user_denied_on_next_decide(app_engine, owner_engine):
    w, req, _ = await approval_world(
        owner_engine, app_engine, levels=[{"role": "manager", "timeout_minutes": 60}]
    )
    step, *_ = await pending_step(owner_engine, req)
    user = await staff(owner_engine, w.org, "manager")
    async with owner_engine.begin() as c:  # demoted between the inbox read and the decide
        await c.execute(
            text("update memberships set role='member' where organization_id=:o and user_id=:u"),
            {"o": w.org, "u": user},
        )
    with pytest.raises(Forbidden):  # catches: role cached from the inbox read
        await decide(app_engine, w, step, user, "manager")


async def test_removed_user_is_not_found(app_engine, owner_engine):
    w, req, _ = await approval_world(
        owner_engine, app_engine, levels=[{"role": "manager", "timeout_minutes": 60}]
    )
    step, *_ = await pending_step(owner_engine, req)
    outsider = await make_user(owner_engine)
    with pytest.raises(NotFoundError):
        await decide(app_engine, w, step, outsider, "org_admin")


async def test_agent_principal_on_a_step_is_forbidden(app_engine, owner_engine):
    w, req, _ = await approval_world(
        owner_engine, app_engine, levels=[{"role": "manager", "timeout_minutes": 60}]
    )
    step, *_ = await pending_step(owner_engine, req)
    async with tenant_session(app_engine, w.org) as s:
        with pytest.raises(Forbidden):  # catches: an agent principal reaching the human path
            await decide_step(
                s,
                w.org,
                NOW,
                keys=KEYS,
                who=AgentPrincipal(w.identity, w.org),
                step_id=step,
                request_version=1,
                approve=True,
            )


async def test_creator_and_agent_owner_denied(app_engine, owner_engine):
    levels = [{"role": "manager", "timeout_minutes": 60}]
    # the agent's owner is a manager and tries to approve their agent's request
    w, req, _ = await approval_world(owner_engine, app_engine, levels=levels)
    step, *_ = await pending_step(owner_engine, req)
    async with owner_engine.begin() as c:
        await c.execute(
            text("update memberships set role='manager' where organization_id=:o and user_id=:u"),
            {"o": w.org, "u": w.owner_user},
        )
    with pytest.raises(Forbidden):  # catches: SoD only in the UI
        await decide(app_engine, w, step, w.owner_user, "manager")
    # the user who created the request cannot decide its steps
    from app.services.decisions import create_decision

    from .helpers import ARGS, cast, voters

    creator = await staff(owner_engine, w.org, "manager")
    async with tenant_session(app_engine, w.org) as s:
        made = await create_decision(s, w.org, NOW, user_id=creator, **ARGS)
    for sess in await voters(owner_engine, w, 3):
        await cast(app_engine, w, made, sess)
    step2, *_ = await pending_step(owner_engine, made)
    with pytest.raises(Forbidden):
        await decide(app_engine, w, step2, creator, "manager")


async def test_same_user_on_a_second_level_is_409_and_the_step_stays_pending(
    app_engine, owner_engine
):
    w, req, _ = await approval_world(owner_engine, app_engine, high=True)
    admin = await staff(owner_engine, w.org, "org_admin")
    step1, *_ = await pending_step(owner_engine, req)
    await decide(
        app_engine, w, step1, admin, "org_admin"
    )  # an org_admin may decide the team_lead level
    step2, level, _ = await pending_step(owner_engine, req)
    assert level == 2
    with pytest.raises(AlreadyDecided):  # S11
        await decide(app_engine, w, step2, admin, "org_admin")
    assert (await pending_step(owner_engine, req))[0] == step2


async def test_step_of_an_older_version_is_409_stale_version(app_engine, owner_engine):
    w, req, _ = await approval_world(
        owner_engine, app_engine, levels=[{"role": "manager", "timeout_minutes": 60}]
    )
    step, *_ = await pending_step(owner_engine, req)
    user = await staff(owner_engine, w.org, "manager")
    with pytest.raises(
        StaleVersion
    ):  # the caller decided against a version that is no longer current
        await decide(app_engine, w, step, user, "manager", version=2)
    async with (
        owner_engine.begin() as c
    ):  # what S11's revise does: bump the version, supersede the step
        await c.execute(
            text("update decision_requests set version=2 where id=:r"), {"r": req["id"]}
        )
        await c.execute(
            text("update approval_steps set status='superseded' where id=:s"), {"s": step}
        )
    for v in (1, 2):
        with pytest.raises(StaleVersion):  # catches: approving a superseded step
            await decide(app_engine, w, step, user, "manager", version=v)


async def test_other_tenants_step_is_not_found(app_engine, owner_engine):
    _, req_a, _ = await approval_world(
        owner_engine, app_engine, levels=[{"role": "manager", "timeout_minutes": 60}]
    )
    step, *_ = await pending_step(owner_engine, req_a)
    b = await make_world(owner_engine)
    user_b = await staff(owner_engine, b.org, "org_admin")
    with pytest.raises(NotFoundError):
        await decide(app_engine, b, step, user_b, "org_admin")
    assert (await pending_step(owner_engine, req_a))[0] == step


async def test_a_decision_after_the_request_left_pending_approval_is_409(app_engine, owner_engine):
    w, req, _ = await approval_world(
        owner_engine, app_engine, levels=[{"role": "manager", "timeout_minutes": 60}]
    )
    step, *_ = await pending_step(owner_engine, req)
    async with owner_engine.begin() as c:
        await c.execute(
            text("update decision_requests set status='cancelled' where id=:r"), {"r": req["id"]}
        )
    with pytest.raises(StepNotPending):
        await decide(app_engine, w, step, await staff(owner_engine, w.org, "manager"), "manager")


async def test_the_same_user_cannot_reverse_a_decision_by_retrying(app_engine, owner_engine):
    w, req, _ = await approval_world(
        owner_engine, app_engine, levels=[{"role": "manager", "timeout_minutes": 60}]
    )
    step, *_ = await pending_step(owner_engine, req)
    user = await staff(owner_engine, w.org, "manager")
    await decide(app_engine, w, step, user, "manager", approve=True)
    with pytest.raises(AlreadyDecided):  # catches: a retry flipping an approval into a rejection
        await decide(app_engine, w, step, user, "manager", approve=False)
    assert (await request_row(owner_engine, req))[0] == "approved"


async def test_manager_and_org_admin_may_decide_a_team_lead_step_org_wide(app_engine, owner_engine):
    for role in ("manager", "org_admin"):
        w, req, _ = await approval_world(owner_engine, app_engine)
        step, _, required = await pending_step(owner_engine, req)
        assert required == "team_lead"
        user = await staff(
            owner_engine, w.org, role
        )  # no team at all: roles above team_lead are org-wide
        assert (await decide(app_engine, w, step, user, role))["status"] == "approved"


@pytest.mark.parametrize("bad", ["a\x00b", "\ud800", "x" * 4097])
async def test_invalid_comment_is_rejected_and_the_step_stays_pending(
    app_engine, owner_engine, bad
):
    from app.domain.errors import InvalidInput

    w, req, _ = await approval_world(
        owner_engine, app_engine, levels=[{"role": "manager", "timeout_minutes": 60}]
    )
    step, *_ = await pending_step(owner_engine, req)
    user = await staff(owner_engine, w.org, "manager")
    with pytest.raises(InvalidInput):
        await decide(app_engine, w, step, user, "manager", comment=bad)
    assert (await pending_step(owner_engine, req))[0] == step


@pytest.mark.parametrize("version", [True, 1.0, "1", None])
async def test_request_version_must_be_a_plain_int(app_engine, owner_engine, version):
    from app.domain.errors import InvalidInput

    w, req, _ = await approval_world(
        owner_engine, app_engine, levels=[{"role": "manager", "timeout_minutes": 60}]
    )
    step, *_ = await pending_step(owner_engine, req)
    with pytest.raises(InvalidInput):  # catches: True passing as version 1
        await decide(
            app_engine,
            w,
            step,
            await staff(owner_engine, w.org, "manager"),
            "manager",
            version=version,
        )


async def test_a_replay_has_the_same_shape_as_the_first_answer(app_engine, owner_engine):
    w, req, _ = await approval_world(
        owner_engine, app_engine, levels=[{"role": "manager", "timeout_minutes": 60}]
    )
    step, *_ = await pending_step(owner_engine, req)
    user = await staff(owner_engine, w.org, "manager")
    first = await decide(app_engine, w, step, user, "manager")
    again = await decide(app_engine, w, step, user, "manager")
    assert set(first) == set(again) and again["replayed"] and not first["replayed"]


async def test_a_removed_user_gets_nothing_not_even_a_replay(app_engine, owner_engine):
    w, req, _ = await approval_world(
        owner_engine, app_engine, levels=[{"role": "manager", "timeout_minutes": 60}]
    )
    step, *_ = await pending_step(owner_engine, req)
    user = await staff(owner_engine, w.org, "manager")
    await decide(app_engine, w, step, user, "manager")
    async with owner_engine.begin() as c:  # removed from the organization afterwards
        await c.execute(
            text("delete from memberships where organization_id=:o and user_id=:u"),
            {"o": w.org, "u": user},
        )
    with pytest.raises(
        NotFoundError
    ):  # catches: replaying a decision (and the request status) to a non-member
        await decide(app_engine, w, step, user, "manager")

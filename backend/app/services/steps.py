import uuid
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz.duties import StepFacts, may_decide
from app.authz.matrix import Role
from app.authz.principals import Principal, UserPrincipal
from app.domain.errors import (
    AlreadyDecided,
    Forbidden,
    InvalidInput,
    NotFoundError,
    StaleVersion,
    StepNotPending,
)
from app.domain.receipt import KeyRing, Subject, sign
from app.domain.statuses import Risk, Status
from app.domain.transitions import Event, top_level, transition
from app.ports.clock import Clock
from app.services import audit, outbox
from app.services.status import change_status
from app.services.validation import check_text

_STEP = text(
    "select id, decision_request_id, request_version, level, required_role, status, approver_user_id"
    " from approval_steps where organization_id = :o and id = :s"
)
# The request row is locked first, here and in every writer (S12's timeout job too): one lock order.
_LOCK_REQUEST = text(
    "select id, status, version, content_hash, effective_risk_level, requested_by_user_id,"
    " requested_by_session_id, policy_snapshot from decision_requests"
    " where organization_id = :o and id = :r for update"
)
_MEMBER = text("select role, team_id from memberships where organization_id = :o and user_id = :u")
_AGENT_OWNER = text(
    "select i.owner_user_id from agent_sessions s join agent_identities i"
    " on i.organization_id = s.organization_id and i.id = s.agent_identity_id"
    " where s.organization_id = :o and s.id = :s"
)
_TEAMS = text("select id, parent_team_id from teams where organization_id = :o")
_DECIDERS = text(
    "select approver_user_id, status from approval_steps where organization_id = :o"
    " and decision_request_id = :r and request_version = :v and status in ('approved', 'rejected')"
)
_DECIDE = text(
    "update approval_steps set status = :new, approver_user_id = :u, comment = :c, decided_at = :now"
    " where organization_id = :o and id = :s and status = 'pending' returning id"
)
_INBOX_FIELDS = (
    "id", "decision_request_id", "request_version", "level", "required_role", "due_at", "title",
    "effective_risk_level", "requested_by_agent_id",
)  # fmt: skip
_OPEN_STEPS = text(
    "select s.id, s.decision_request_id, s.request_version, s.level, s.required_role, s.due_at, d.title,"
    " d.effective_risk_level, a.agent_identity_id as requested_by_agent_id, d.requested_by_user_id,"
    " d.requested_by_session_id, d.policy_snapshot from approval_steps s join decision_requests d"
    " on d.organization_id = s.organization_id and d.id = s.decision_request_id"
    " left join agent_sessions a on a.organization_id = d.organization_id and a.id = d.requested_by_session_id"
    " where s.organization_id = :o and s.status = 'pending' and d.status = 'pending_approval'"
    " and s.request_version = d.version order by s.due_at, s.id"
)
_INSERT_STEP = text(
    "insert into approval_steps (organization_id, decision_request_id, request_version, level,"
    " required_role, due_at, stands_in_for_level) values (:o, :r, :v, :level, :role, :due, :stands) returning id"
)


async def open_step(
    session: AsyncSession, o: uuid.UUID, request_id: uuid.UUID, version: int,
    level: int, snapshot: dict[str, Any], now: datetime, stands_in_for: int | None = None,
) -> uuid.UUID:  # fmt: skip
    spec = snapshot["levels"][level - 1]
    step_id = (
        await session.execute(
            _INSERT_STEP,
            {
                "o": o, "r": request_id, "v": version, "level": level, "role": spec["role"],
                "due": now + timedelta(minutes=spec["timeout_minutes"]), "stands": stands_in_for,
            },
        )
    ).scalar_one()  # fmt: skip
    data = {"decision_request_id": str(request_id), "request_version": version, "level": level,
            "required_role": spec["role"], "stands_in_for_level": stands_in_for}  # fmt: skip
    await audit.record(session, o, "approval_step", step_id, "created", "system", None, data)
    await outbox.emit(session, o, "step.created", data | {"id": str(step_id)})
    return step_id


async def open_first_step(
    session: AsyncSession, o: uuid.UUID, request_id: uuid.UUID, version: int,
    snapshot: dict[str, Any], now: datetime,
) -> uuid.UUID:  # fmt: skip
    """Level 1 opens when a request enters pending_approval; later levels open as each one approves."""
    return await open_step(session, o, request_id, version, 1, snapshot, now)


def _role(name: str) -> Role:
    return Role[name.upper()]


async def _teams(session: AsyncSession, o: uuid.UUID) -> dict[uuid.UUID, uuid.UUID | None]:
    return {t.id: t.parent_team_id for t in (await session.execute(_TEAMS, {"o": o})).all()}


def _led(role: Role, team_id: uuid.UUID | None) -> frozenset[uuid.UUID]:
    return frozenset({team_id}) if role is Role.TEAM_LEAD and team_id else frozenset()


async def _facts(
    session: AsyncSession, o: uuid.UUID, req: Any, required_role: str,
    prior: frozenset[uuid.UUID], teams: dict[uuid.UUID, uuid.UUID | None],
) -> StepFacts:  # fmt: skip
    """What separation of duties needs about a request; shared by deciding and by the inbox."""
    owner_id = None
    if req["requested_by_session_id"] is not None:
        owner_id = (
            await session.execute(_AGENT_OWNER, {"o": o, "s": req["requested_by_session_id"]})
        ).scalar()
    owner_team = req["policy_snapshot"].get("owner_team_id")
    return StepFacts(
        organization_id=o,
        required_role=_role(required_role),
        requested_by_user_id=req["requested_by_user_id"],
        agent_owner_id=owner_id,
        request_team_id=uuid.UUID(owner_team) if owner_team else None,
        team_parents=teams,
        prior_decider_ids=prior,
    )


async def pending_for(session: AsyncSession, o: uuid.UUID, user_id: uuid.UUID) -> list[dict]:
    """Pending steps of current versions that this user may decide now, by due time (the same rule as
    decide_step, with role and team read from the database)."""
    member = (await session.execute(_MEMBER, {"o": o, "u": user_id})).first()
    if member is None:
        return []
    role = _role(member.role)
    me, led, teams = (
        UserPrincipal(user_id, o, role),
        _led(role, member.team_id),
        await _teams(session, o),
    )
    out = []
    # ponytail: a few queries per open step, filtered in Python; push into SQL if inboxes get long.
    for row in (await session.execute(_OPEN_STEPS, {"o": o})).mappings():
        deciders = await session.execute(
            _DECIDERS, {"o": o, "r": row["decision_request_id"], "v": row["request_version"]}
        )
        prior = frozenset(d.approver_user_id for d in deciders)
        if may_decide(me, await _facts(session, o, row, row["required_role"], prior, teams), led):
            out.append({k: row[k] for k in _INBOX_FIELDS})
    return out


async def decide_step(
    session: AsyncSession,
    organization_id: uuid.UUID,
    clock: Clock,
    *,
    keys: KeyRing,
    who: Principal,
    step_id: uuid.UUID,
    request_version: int,
    approve: bool,
    comment: str = "",
) -> dict[str, Any]:
    """A user approves or rejects the pending step; the top level approving approves the request.

    Authority is rechecked here from the database (role and team now, not as listed in an inbox), and the
    request row lock makes concurrent deciders take turns: the loser finds the step no longer pending."""
    o = organization_id
    if type(request_version) is not int:  # True == 1, 1.0 == 1: neither is a version
        raise InvalidInput("request_version must be an int")
    if not isinstance(who, UserPrincipal) or who.organization_id != o:
        raise Forbidden(
            "only users of this organization decide steps"
        )  # agents never decide human steps
    check_text("comment", comment, 0, 4096)
    first = (await session.execute(_STEP, {"o": o, "s": step_id})).mappings().first()
    if first is None:
        raise NotFoundError
    req = (
        (await session.execute(_LOCK_REQUEST, {"o": o, "r": first["decision_request_id"]}))
        .mappings()
        .one()
    )
    step = (
        (await session.execute(_STEP, {"o": o, "s": step_id})).mappings().one()
    )  # fresh, under the lock
    decision = Status.APPROVED if approve else Status.REJECTED

    # Membership first: a user who is no longer in the organization gets nothing, not even a replay.
    member = (await session.execute(_MEMBER, {"o": o, "u": who.user_id})).first()
    if member is None:
        raise NotFoundError

    if step["status"] in ("approved", "rejected") and step["approver_user_id"] == who.user_id:
        if step["status"] != decision.value:
            raise AlreadyDecided  # a retry may repeat a decision, not reverse it
        return {
            "step_id": step_id,
            "status": step["status"],
            "request_status": req["status"],
            "next_step_id": None,
            "replayed": True,
        }
    if (
        request_version != req["version"]
        or step["status"] == "superseded"
        or step["request_version"] != req["version"]
    ):
        raise StaleVersion
    if step["status"] != "pending" or req["status"] != Status.PENDING_APPROVAL.value:
        raise StepNotPending

    role, team_id = _role(member.role), member.team_id
    fresh = UserPrincipal(who.user_id, o, role)  # the claimed role in `who` is never trusted
    snapshot = req["policy_snapshot"]
    deciders = (
        await session.execute(_DECIDERS, {"o": o, "r": req["id"], "v": req["version"]})
    ).all()
    prior = frozenset(d.approver_user_id for d in deciders)
    if who.user_id in prior:
        raise AlreadyDecided  # one decision per user and version (S11): 409, the step stays pending
    facts = await _facts(session, o, req, step["required_role"], prior, await _teams(session, o))
    if not may_decide(fresh, facts, _led(role, team_id)):
        raise Forbidden("not allowed to decide this step")

    now = clock.now()
    done = await session.execute(
        _DECIDE,
        {
            "o": o,
            "s": step_id,
            "new": decision.value,
            "u": who.user_id,
            "c": comment or None,
            "now": now,
        },
    )
    if done.first() is None:  # unreachable under the lock; never double-apply if it is not
        raise StepNotPending
    data = {"decision_request_id": str(req["id"]), "request_version": req["version"],
            "level": step["level"], "decision": decision.value}  # fmt: skip
    await audit.record(session, o, "approval_step", step_id, "decided", "user", who.user_id, data)
    await outbox.emit(session, o, "step.decided", data | {"id": str(step_id)})

    status = Status(req["status"])
    next_step = None
    if not approve:
        await change_status(
            session,
            o,
            req["id"],
            req["version"],
            status,
            transition(status, Event.STEP_REJECTED),
            now,
            None,
            "user",
            who.user_id,
        )
        status = Status.REJECTED
    elif step["level"] >= top_level(Risk.of(req["effective_risk_level"]), len(snapshot["levels"])):
        approvers = tuple(d.approver_user_id for d in deciders if d.status == "approved") + (
            who.user_id,
        )
        receipt = sign(
            Subject(req["id"], req["version"], req["content_hash"], now, approvers), keys
        )
        new = transition(status, Event.STEP_APPROVED_TOP)
        await change_status(
            session, o, req["id"], req["version"], status, new, now, receipt, "user", who.user_id
        )
        status = new
    else:
        next_step = await open_step(
            session, o, req["id"], req["version"], step["level"] + 1, snapshot, now
        )
    return {
        "step_id": step_id, "status": decision.value, "request_status": status.value,
        "next_step_id": next_step, "replayed": False,
    }  # fmt: skip

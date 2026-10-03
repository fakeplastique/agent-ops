import json
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.domain.receipt import KeyRing


class FakeClock:
    def __init__(self, now: datetime) -> None:
        self._now = now

    def now(self) -> datetime:
        return self._now


def utc(*args: int) -> datetime:
    return datetime(*args, tzinfo=UTC)


async def make_org(owner: AsyncEngine, plan_key: str = "free") -> uuid.UUID:
    # The owner skips RLS, so a test can plant any tenant without a tenant context.
    org = uuid.uuid4()
    async with owner.begin() as c:
        await c.execute(
            text("insert into organizations (id, name, slug, plan_key) values (:i, 'n', :s, :p)"),
            {"i": org, "s": f"o-{org}", "p": plan_key},
        )
    return org


async def set_usage(owner: AsyncEngine, org: uuid.UUID, period: str, value: int) -> None:
    async with owner.begin() as c:
        await c.execute(
            text(
                "insert into usage_counters (organization_id, metric, period, value)"
                " values (:o, 'requests', :p, :v)"
            ),
            {"o": org, "p": period, "v": value},
        )


async def _exec(owner: AsyncEngine, sql: str, **params) -> None:
    async with owner.begin() as c:
        await c.execute(text(sql), params)


async def make_user(owner: AsyncEngine) -> uuid.UUID:
    uid = uuid.uuid4()
    await _exec(
        owner,
        "insert into users (id, email, full_name) values (:i, :e, 'U')",
        i=uid,
        e=f"{uid}@x.test",
    )
    return uid


async def make_team(owner, org, parent=None, name=None) -> uuid.UUID:
    tid = uuid.uuid4()
    await _exec(
        owner,
        "insert into teams (id, organization_id, name, parent_team_id) values (:i, :o, :n, :p)",
        i=tid,
        o=org,
        n=name or f"t-{tid}",
        p=parent,
    )
    return tid


async def make_member(owner, org, user, role="member", team=None) -> None:
    await _exec(
        owner,
        "insert into memberships (organization_id, user_id, team_id, role) values (:o, :u, :t, :r)",
        o=org,
        u=user,
        t=team,
        r=role,
    )


async def make_policy(
    owner,
    org,
    team=None,
    *,
    quorum=3,
    rounds=1,
    threshold=0.8,
    levels=None,
    timeout=60,
    floors=None,
    distinct=True,
) -> uuid.UUID:
    pid = uuid.uuid4()
    await _exec(
        owner,
        "insert into escalation_policies (id, organization_id, team_id, name, quorum_size, rounds,"
        " confidence_threshold, levels, voting_timeout_minutes, risk_floors, require_distinct_agents)"
        " values (:i, :o, :t, :n, :q, :r, :c, cast(:l as jsonb), :to, cast(:f as jsonb), :d)",
        i=pid,
        o=org,
        t=team,
        n=f"p-{pid}",
        q=quorum,
        r=rounds,
        c=threshold,
        to=timeout,
        l=json.dumps(levels or [{"role": "manager", "timeout_minutes": 60}]),
        f=json.dumps(floors or {}),
        d=distinct,
    )
    return pid


async def make_agent(owner, org, owner_user, status="active") -> tuple[uuid.UUID, uuid.UUID]:
    ident, sess = uuid.uuid4(), uuid.uuid4()
    await _exec(
        owner,
        "insert into agent_identities (id, organization_id, name, owner_user_id, status)"
        " values (:i, :o, :n, :u, :s)",
        i=ident,
        o=org,
        n=f"a-{ident}",
        u=owner_user,
        s=status,
    )
    await _exec(
        owner,
        "insert into agent_sessions (id, organization_id, agent_identity_id, session_label)"
        " values (:s, :o, :i, 'l')",
        s=sess,
        o=org,
        i=ident,
    )
    return ident, sess


@dataclass
class World:
    org: uuid.UUID
    owner_user: uuid.UUID
    identity: uuid.UUID
    session: uuid.UUID
    policy: uuid.UUID


async def make_world(owner, plan="free", **policy) -> World:
    org = await make_org(owner, plan)
    user = await make_user(owner)
    await make_member(owner, org, user, "member")
    ident, sess = await make_agent(owner, org, user)
    return World(org, user, ident, sess, await make_policy(owner, org, **policy))


KEYS = KeyRing.parse(f"t1:{'k' * 40}")


async def add_agent(owner, w: "World") -> tuple[uuid.UUID, uuid.UUID]:
    """Another agent identity (own session) in the world's organization."""
    return await make_agent(owner, w.org, w.owner_user)


NOW = FakeClock(utc(2026, 1, 15, 10, 0))
ARGS = {
    "title": "Refund order 7",
    "description": "d",
    "action_type": "refund",
    "payload": {"amount": 5},
    "risk_level": "low",
}


async def open_request(engine, w: "World", clock=NOW, **over) -> dict:
    from app.db.session import tenant_session
    from app.services.decisions import create_decision

    kw: dict[str, Any] = {"agent_session_id": w.session} | ARGS | over
    async with tenant_session(engine, w.org) as s:
        return await create_decision(s, w.org, clock, **kw)


async def cast(
    engine, w: "World", req: dict, session, verdict="approve", confidence=0.9, clock=NOW, **over
) -> dict:
    from app.db.session import tenant_session
    from app.services.decisions import cast_vote

    kw = {
        "keys": KEYS,
        "decision_request_id": req["id"],
        "agent_session_id": session,
        "request_version": req["version"],
        "verdict": verdict,
        "confidence": confidence,
        "rationale": "because",
    } | over
    async with tenant_session(engine, w.org) as s:
        return await cast_vote(s, w.org, clock, **kw)


async def voters(owner, w: "World", n: int) -> list[uuid.UUID]:
    """n sessions, each of its own agent identity (the requester's identity is not among them)."""
    return [(await add_agent(owner, w))[1] for _ in range(n)]


async def fetch(owner, sql: str, **params) -> list:
    async with owner.connect() as c:
        return (await c.execute(text(sql), params)).all()


LEVELS3 = [
    {"role": "team_lead", "timeout_minutes": 30},
    {"role": "manager", "timeout_minutes": 60},
    {"role": "org_admin", "timeout_minutes": 120},
]


async def staff(owner, org, role, team=None) -> uuid.UUID:
    """A user with a membership of this role (and team) in the organization."""
    user = await make_user(owner)
    await make_member(owner, org, user, role, team)
    return user


async def approval_world(owner, app_engine, *, high=False, levels=None, plan="pro", team=True):
    """A request sitting in pending_approval with its first step created.

    Returns (world, request, team_id). The requester agent's owner is a member of `team`."""
    floors = {"refund": "high" if high else "medium"}
    w = await make_world(owner, plan, floors=floors, levels=levels or LEVELS3)
    team_id = await make_team(owner, w.org) if team else None
    if team_id:
        async with owner.begin() as c:
            await c.execute(
                text("update memberships set team_id=:t where organization_id=:o and user_id=:u"),
                {"t": team_id, "o": w.org, "u": w.owner_user},
            )
    req = await open_request(app_engine, w)
    for s in await voters(owner, w, 3):
        await cast(app_engine, w, req, s)
    return w, req, team_id


async def pending_step(owner, req) -> tuple[uuid.UUID, int, str]:
    rows = await fetch(
        owner,
        "select id, level, required_role from approval_steps where decision_request_id=:r and status='pending'",
        r=req["id"],
    )
    assert len(rows) == 1, rows
    return rows[0]


async def decide(
    engine, w, step_id, user, role="org_admin", approve=True, comment="", version=1, clock=NOW
):
    from app.authz.matrix import Role
    from app.authz.principals import UserPrincipal
    from app.db.session import tenant_session
    from app.services.steps import decide_step

    # The claimed role is deliberately arbitrary: the service must read the real one.
    who = UserPrincipal(user, w.org, Role[role.upper()])
    async with tenant_session(engine, w.org) as s:
        return await decide_step(
            s, w.org, clock, keys=KEYS, who=who, step_id=step_id, request_version=version,
            approve=approve, comment=comment,
        )  # fmt: skip

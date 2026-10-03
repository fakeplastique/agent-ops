import json
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine


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
) -> uuid.UUID:
    pid = uuid.uuid4()
    await _exec(
        owner,
        "insert into escalation_policies (id, organization_id, team_id, name, quorum_size, rounds,"
        " confidence_threshold, levels, voting_timeout_minutes, risk_floors)"
        " values (:i, :o, :t, :n, :q, :r, :c, cast(:l as jsonb), :to, cast(:f as jsonb))",
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

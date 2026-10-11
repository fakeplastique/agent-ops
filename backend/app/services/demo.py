"""Demo data (S35): a fixed roster of two organizations seeded through the services, and the demo sign-in.

Every write goes through the same services the API uses, so the seeded states carry their audit rows, outbox
events, usage counts and receipts. A re-run only adds what is missing and never mints a second secret."""

import uuid
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.authz.matrix import Role
from app.authz.principals import UserPrincipal
from app.config import settings
from app.db.session import tenant_session
from app.domain.errors import NotFoundError
from app.domain.receipt import KeyRing
from app.ports.clock import Clock
from app.services import (
    agents,
    decisions,
    escalation,
    invitations,
    members,
    plans,
    policies,
    sessions,
    steps,
    teams,
    web_sessions,
    webhooks,
)
from app.services.webhook_security import check_url

_NAMESPACE = uuid.UUID("5e0d1f0e-7c0a-4c3e-9a55-3b1d2f6a9c11")
WEEK = 7 * 24 * 60  # minutes: seeded open work stays open for a week, not until the next sweep
REQUESTER, VOTERS = "Refund Bot", ("Risk Reviewer", "Policy Checker", "Fraud Screen")
SESSION_LABEL = "demo"
HOOK_EVENTS = ["decision.created", "decision.status_changed", "step.created", "step.decided"]


@dataclass(frozen=True)
class DemoUser:
    email: str
    name: str
    role: str
    team: str | None = None


@dataclass(frozen=True)
class DemoOrg:
    slug: str
    name: str
    plan: str
    users: tuple[DemoUser, ...]
    teams: tuple[tuple[str, str | None], ...]  # (name, parent name), parents first
    levels: int  # escalation levels in the default policy (Free allows 1)
    rounds: int
    hooks: tuple[tuple[str, list[str], bool], ...]  # (url, events, active)


ORGS = (
    DemoOrg(
        "acme-retail", "Acme Retail", "pro",
        (
            DemoUser("admin@acme.demo", "Ada Okafor", "org_admin"),
            DemoUser("manager@acme.demo", "Marcus Lind", "manager"),
            DemoUser("lead@acme.demo", "Tess Varga", "team_lead", "Support"),
            DemoUser("member@acme.demo", "Mia Chen", "member", "Support"),
        ),
        (("Operations", None), ("Support", "Operations")),
        3, 2,
        (
            ("https://example.com/hooks/agent-ops", HOOK_EVENTS, True),
            ("https://example.com/hooks/billing", ["plan.changed"], False),
        ),
    ),
    DemoOrg(
        "birch-studio", "Birch Studio", "free",
        (
            DemoUser("admin@birch.demo", "Boris Held", "org_admin"),
            DemoUser("manager@birch.demo", "Mina Ortiz", "manager"),
            DemoUser("lead@birch.demo", "Theo Rask", "team_lead", "Support"),
            DemoUser("member@birch.demo", "Nell Abara", "member", "Support"),
        ),
        (("Support", None),),
        1, 1,
        (("https://example.com/hooks/birch", HOOK_EVENTS, False),),
    ),
)  # fmt: skip


@dataclass(frozen=True)
class Scenario:
    title: str
    action: str
    risk: str
    votes: tuple[tuple[str, float], ...]  # verdict, confidence; at most the quorum
    decide: tuple[tuple[str, bool], ...] = ()  # (role, approve) for the pending step, in order
    end: str = ""  # "expire" or "withdraw"
    pro_only: bool = False  # needs the second round or a second level


A, R = "approve", "reject"
ALL_A = ((A, 0.9),) * 3
# One request per status (Pro adds the discussion round and a second level). The Pro policy floors
# refund at medium, delete_data at high and send_receipt_email at low (only a low request auto-approves).
SCENARIOS = (
    Scenario("Refund order #1042", "refund", "medium", ((A, 0.9), (A, 0.8))),  # pending_votes
    Scenario("Refund order #1043", "refund", "medium", ((A, 0.9), (A, 0.7), (R, 0.6)), pro_only=True),
    Scenario("Refund order #1044", "refund", "medium", ALL_A),  # pending_approval
    Scenario("Delete customer export #88", "delete_data", "high", ALL_A, (("team_lead", True),), pro_only=True),
    Scenario("Send receipt email #5521", "send_receipt_email", "low", ((A, 0.95),) * 3),  # auto-approved
    Scenario("Refund order #1040", "refund", "medium", ALL_A, (("team_lead", True),)),  # approved by a human
    Scenario("Refund order #1039", "refund", "medium", ((R, 0.9),) * 3),  # rejected
    Scenario("Refund order #1031", "refund", "medium", ALL_A, end="expire"),
    Scenario("Refund order #1030", "refund", "medium", ((A, 0.9),), end="withdraw"),  # cancelled
)  # fmt: skip


@dataclass
class Seeded:
    secrets: dict[str, str] = field(
        default_factory=dict
    )  # shown once: agent tokens, webhook secrets


def org_id(slug: str) -> uuid.UUID:
    """A stable id per demo organization: the demo sign-in finds it without a cross-tenant lookup."""
    return uuid.uuid5(_NAMESPACE, slug)


def roster() -> list[tuple[DemoOrg, DemoUser]]:
    """Demo users who may sign in: never one whose email carries platform-admin powers."""
    barred = plans.allow_list(settings.platform_admin_emails)
    return [(o, u) for o in ORGS for u in o.users if u.email not in barred]


async def seeded_users(engine: AsyncEngine) -> list[dict[str, str]]:
    """The roster users that exist now (an unseeded database lists nobody)."""
    return [
        {"email": u.email, "name": u.name, "role": u.role, "organization": o.name}
        for o, u in roster()
        if await web_sessions.find_user(engine, u.email) is not None
    ]


async def sign_in(
    engine: AsyncEngine, clock: Clock, email: str, previous: str | None
) -> tuple[str, uuid.UUID, uuid.UUID, str]:
    """A session for a seeded roster user, with their organization selected: (cookie, user, org, role)."""
    found = next(((o, u) for o, u in roster() if u.email == email), None)
    user = await web_sessions.find_user(engine, email) if found else None
    if found is None or user is None:
        raise NotFoundError
    org = org_id(found[0].slug)
    raw = await web_sessions.start_session(engine, user, clock, previous)
    return raw, user, org, await web_sessions.select_organization(engine, raw, user, org)


async def seed(engine: AsyncEngine, clock: Clock, keys: KeyRing | None = None) -> Seeded:
    """Build both organizations. `keys` sign the receipts (the configured ring by default)."""
    keys = keys or KeyRing.parse(settings.receipt_keys)
    out = Seeded()
    for org in ORGS:
        await _seed_org(engine, clock, keys, org, out)
    return out


async def _exists(engine: AsyncEngine, org: uuid.UUID) -> bool:
    async with tenant_session(engine, org) as s:
        return await s.scalar(text("select count(*) from organizations")) == 1


async def _seed_org(
    engine: AsyncEngine, clock: Clock, keys: KeyRing, spec: DemoOrg, out: Seeded
) -> None:
    org = org_id(spec.slug)
    ids = {
        u.email: await web_sessions.find_or_create_user(engine, u.email, u.name) for u in spec.users
    }
    admin = ids[next(u.email for u in spec.users if u.role == "org_admin")]
    if not await _exists(engine, org):
        await members.create_organization(engine, admin, spec.name, spec.slug, org)
        if spec.plan != "free":  # before the policy: a Free org cannot hold a three-level one
            async with tenant_session(engine, org) as s:
                await plans.change_plan(s, org, spec.plan, admin, "demo")

    async with tenant_session(engine, org) as s:
        team_ids = await _teams(s, org, admin, spec)
    for u in spec.users:
        if u.role != "org_admin" and await web_sessions.role_in(engine, ids[u.email], org) is None:
            async with tenant_session(engine, org) as s:
                _, raw = await invitations.create_invitation(
                    s, org, admin, u.email, u.role, team_ids.get(u.team or ""), clock
                )
            await invitations.accept_invitation(engine, ids[u.email], raw, clock)

    owner = ids[next(u.email for u in spec.users if u.role == "member")]
    async with tenant_session(engine, org) as s:
        await _policy(s, org, admin, spec)
        agent_ids = await _agents(s, org, admin, owner, spec, out)
        await _webhooks(s, org, admin, spec, out)

    users = {u.role: ids[u.email] for u in spec.users}
    for sc in SCENARIOS:
        if (spec.plan == "pro" or not sc.pro_only) and not await _titled(engine, org, sc.title):
            async with tenant_session(
                engine, org
            ) as s:  # one transaction: a scenario is whole or absent
                await _scenario(s, org, clock, keys, sc, agent_ids, users)


async def _teams(
    s: AsyncSession, org: uuid.UUID, admin: uuid.UUID, spec: DemoOrg
) -> dict[str, Any]:
    have = {t["name"]: t["id"] for t in (await teams.list_teams(s, org, 0, 100))[0]}
    for name, parent in spec.teams:
        if name not in have:
            have[name] = (await teams.create_team(s, org, admin, name, have.get(parent or "")))[
                "id"
            ]
    return have


async def _policy(s: AsyncSession, org: uuid.UUID, admin: uuid.UUID, spec: DemoOrg) -> None:
    if any(p["team_id"] is None for p in (await policies.list_policies(s, org, 0, 100))[0]):
        return
    roles = ("team_lead", "manager", "org_admin")[: spec.levels]
    await policies.create_policy(
        s, org, admin, None,
        {
            "name": "Default policy", "quorum_size": 3, "require_distinct_agents": True,
            "rounds": spec.rounds, "confidence_threshold": 0.8, "voting_timeout_minutes": WEEK,
            "levels": [{"role": r, "timeout_minutes": WEEK} for r in roles],
            "risk_floors": {"refund": "medium", "delete_data": "high", "send_receipt_email": "low"},
        },
    )  # fmt: skip


async def _agents(
    s: AsyncSession, org: uuid.UUID, admin: uuid.UUID, owner: uuid.UUID, spec: DemoOrg, out: Seeded
) -> dict[str, uuid.UUID]:
    have = {a["name"]: a["id"] for a in (await agents.list_agents(s, org, 0, 100))[0]}
    for name in (REQUESTER, *VOTERS):
        if name not in have:
            row, token = await agents.create_agent(
                s, org, admin, name, f"Demo agent: {name}", "Demo Labs", owner
            )
            have[name] = row["id"]
            out.secrets[f"{spec.slug}/agent/{name}"] = token
    return have


async def _webhooks(
    s: AsyncSession, org: uuid.UUID, admin: uuid.UUID, spec: DemoOrg, out: Seeded
) -> None:
    have = {w["url"] for w in (await webhooks.list_subscriptions(s, org, 0, 100))[0]}
    for url, events, active in spec.hooks:
        url = check_url(url).url  # the same guard as the API, without the DNS lookup
        if url in have:
            continue
        row, secret = await webhooks.create_subscription(s, org, admin, url, events)
        if not active:
            await webhooks.update_subscription(s, org, admin, row["id"], {"active": False})
        out.secrets[f"{spec.slug}/webhook/{url}"] = secret


async def _titled(engine: AsyncEngine, org: uuid.UUID, title: str) -> bool:
    async with tenant_session(engine, org) as s:
        found = await s.scalar(
            text(
                "select count(*) from decision_requests where organization_id = :o and title = :t"
            ),
            {"o": org, "t": title},
        )
    return bool(found)


_PENDING_STEP = text(
    "select id from approval_steps where organization_id = :o and decision_request_id = :r"
    " and status = 'pending'"
)


async def _scenario(
    s: AsyncSession, org: uuid.UUID, clock: Clock, keys: KeyRing, sc: Scenario,
    agent_ids: dict[str, uuid.UUID], users: dict[str, uuid.UUID],
) -> None:  # fmt: skip
    sess = {
        name: (
            await sessions.get_or_create_session(s, org, i, clock, argument_label=SESSION_LABEL)
        )[0]
        for name, i in agent_ids.items()
    }
    req = await decisions.create_decision(
        s, org, clock, title=sc.title, description=f"Demo request: {sc.title.lower()}.",
        action_type=sc.action, payload={"ref": sc.title.split("#")[-1], "amount_cents": 4250},
        risk_level=sc.risk, agent_session_id=sess[REQUESTER],
    )  # fmt: skip
    for voter, (verdict, confidence) in zip(VOTERS, sc.votes):
        await decisions.cast_vote(
            s, org, clock, keys=keys, decision_request_id=req["id"], agent_session_id=sess[voter],
            request_version=req["version"], verdict=verdict, confidence=confidence,
            rationale="Matches the written policy." if verdict == A else "Outside the policy.",
        )  # fmt: skip
    for role, approve in sc.decide:
        step = (await s.execute(_PENDING_STEP, {"o": org, "r": req["id"]})).scalar_one()
        who = UserPrincipal(users[role], org, Role[role.upper()])
        await steps.decide_step(
            s, org, clock, keys=keys, who=who, step_id=step, request_version=req["version"],
            approve=approve, comment="Looks right.",
        )  # fmt: skip
    if sc.end == "withdraw":
        await decisions.withdraw_decision(
            s, org, clock, decision_request_id=req["id"], agent_session_id=sess[REQUESTER]
        )
    while sc.end == "expire":  # each timed-out level opens a stand-in until the last one expires it
        step = (await s.execute(_PENDING_STEP, {"o": org, "r": req["id"]})).scalar_one()
        await escalation.escalate_step(s, org, step, clock.now())
        if not (await s.execute(_PENDING_STEP, {"o": org, "r": req["id"]})).first():
            break

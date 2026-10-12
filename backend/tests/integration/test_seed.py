import pytest
from sqlalchemy import text

from app.config import settings
from app.db.session import tenant_session
from app.domain.statuses import Status
from app.services import demo
from tests.integration.helpers import KEYS, FakeClock, fetch, utc

pytestmark = pytest.mark.anyio

# Far past every other test's clock: seeded deadlines never fall due in a sweep that is not ours.
CLOCK = FakeClock(utc(2200, 1, 1))
ACME, BIRCH = (demo.org_id(o.slug) for o in demo.ORGS)
COUNTED = (
    "memberships", "teams", "agent_identities", "agent_tokens", "escalation_policies",
    "decision_requests", "votes", "approval_steps", "webhook_subscriptions", "audit_logs",
    "outbox_events", "usage_counters",
)  # fmt: skip
ROLES = {"member", "team_lead", "manager", "org_admin"}


async def _wipe(owner) -> None:
    """Remove every seeded row (the owner skips RLS and, as a replica, FKs): other tests share this database."""
    async with owner.begin() as c:
        await c.execute(text("set local session_replication_role = replica"))
        tables = await c.scalars(
            text(
                "select table_name from information_schema.columns where table_schema = 'public'"
                " and column_name = 'organization_id' and table_name <> 'organizations'"
            )
        )
        for t in tables.all():
            await c.execute(
                text(f"delete from {t} where organization_id = any(:i)"), {"i": [ACME, BIRCH]}
            )
        await c.execute(text("delete from organizations where id = any(:i)"), {"i": [ACME, BIRCH]})
        demo_users = "select id from users where email like '%.demo'"
        await c.execute(text(f"delete from web_sessions where user_id in ({demo_users})"))
        await c.execute(text("delete from users where email like '%.demo'"))


@pytest.fixture
async def seeded(app_engine, owner_engine):
    await _wipe(owner_engine)
    yield await demo.seed(app_engine, CLOCK, KEYS)
    await _wipe(owner_engine)


async def _one(owner, sql: str, **params):
    return (await fetch(owner, sql, **params))[0][0]


async def _counts(owner) -> dict[str, int]:
    both = [ACME, BIRCH]
    out = {
        t: await _one(owner, f"select count(*) from {t} where organization_id = any(:i)", i=both)
        for t in COUNTED
    }
    return out | {
        "users": await _one(owner, "select count(*) from users where email like '%.demo'")
    }


async def _column(owner, sql: str, **params) -> set:
    return {r[0] for r in await fetch(owner, sql, **params)}


async def test_seed_is_idempotent_and_reaches_every_status(seeded, app_engine, owner_engine):
    before = await _counts(owner_engine)
    again = await demo.seed(app_engine, CLOCK, KEYS)
    # catches: duplicate rows on a re-run
    assert await _counts(owner_engine) == before
    assert again.secrets == {}  # nothing new, so no token or webhook secret is minted again
    assert seeded.secrets  # the first run hands out every agent token and webhook secret once
    assert before["webhook_subscriptions"] >= 2 and before["agent_tokens"] >= 8

    # catches: a status nobody reaches (the Pro policy has two rounds, so it shows all seven)
    status_sql = "select distinct status from decision_requests where organization_id = :o"
    every = {s.value for s in Status}
    assert await _column(owner_engine, status_sql, o=ACME) == every
    assert await _column(owner_engine, status_sql, o=BIRCH) == every - {"pending_discussion"}
    plans = dict(
        await fetch(
            owner_engine,
            "select id, plan_key from organizations where id = any(:i)",
            i=[ACME, BIRCH],
        )
    )
    assert plans == {ACME: "pro", BIRCH: "free"}
    for org in (ACME, BIRCH):
        roles = await _column(
            owner_engine, "select role from memberships where organization_id = :o", o=org
        )
        assert roles == ROLES


async def test_seed_uses_services_so_audit_outbox_and_receipts_exist(seeded, owner_engine):
    # catches: a raw-SQL seed producing states the engine cannot produce (no trail, no event, no receipt)
    for org in (ACME, BIRCH):
        n = await _one(
            owner_engine, "select count(*) from decision_requests where organization_id = :o", o=org
        )
        created = await _one(
            owner_engine,
            "select count(*) from audit_logs where organization_id = :o"
            " and entity_type = 'decision_request' and action = 'created'",
            o=org,
        )
        assert created == n
        events = await _column(
            owner_engine, "select type from outbox_events where organization_id = :o", o=org
        )
        assert {
            "decision.created",
            "decision.status_changed",
            "vote.cast",
            "step.created",
            "step.decided",
        } <= events
        actions = await _column(
            owner_engine, "select action from audit_logs where organization_id = :o", o=org
        )
        assert {
            "organization_created",
            "invitation_accepted",
            "status_changed",
            "token_issued",
        } <= actions
        approved = await fetch(
            owner_engine,
            "select receipt is not null, decided_at is not null from decision_requests"
            " where organization_id = :o and status = 'approved'",
            o=org,
        )
        assert approved and all(r == (True, True) for r in approved)
    # metered through the engine: one unit per request
    n = await _one(
        owner_engine, "select count(*) from decision_requests where organization_id = :o", o=BIRCH
    )
    used = await _one(
        owner_engine, "select sum(value) from usage_counters where organization_id = :o", o=BIRCH
    )
    assert used == n


async def test_seeded_org_a_data_invisible_to_org_b_users(seeded, app_engine, web, monkeypatch):
    monkeypatch.setattr(settings, "demo_mode", True)
    # in SQL: org B's tenant context sees none of org A's rows
    async with tenant_session(app_engine, BIRCH) as s:
        for t in (
            "decision_requests",
            "agent_identities",
            "memberships",
            "teams",
            "webhook_subscriptions",
        ):
            foreign = await s.scalar(
                text(f"select count(*) from {t} where organization_id = :a"), {"a": ACME}
            )
            assert foreign == 0, t
    # over HTTP: a signed-in org B user lists only org B's requests and cannot open org A's
    login = await web.post("/api/v1/auth/demo/login", json={"email": "admin@birch.demo"})
    assert login.status_code == 200, login.text
    assert login.json()["organization_id"] == str(BIRCH)
    listed = (await web.client.get("/api/v1/decision-requests?limit=100")).json()["items"]
    assert listed
    async with tenant_session(app_engine, ACME) as s:
        acme_ids = {
            str(i) for i in (await s.scalars(text("select id from decision_requests"))).all()
        }
    assert not acme_ids & {d["id"] for d in listed}
    assert (
        await web.client.get(f"/api/v1/decision-requests/{next(iter(acme_ids))}")
    ).status_code == 404


async def test_demo_users_endpoint_404_when_DEMO_MODE_false(seeded, web, monkeypatch):
    # catches: demo login shipped to production
    monkeypatch.setattr(settings, "demo_mode", False)
    assert (await web.client.get("/api/v1/auth/demo/users")).status_code == 404
    off = await web.post("/api/v1/auth/demo/login", json={"email": "admin@acme.demo"})
    assert off.status_code == 404
    assert "set-cookie" not in off.headers

    monkeypatch.setattr(settings, "demo_mode", True)
    users = (await web.client.get("/api/v1/auth/demo/users")).json()["users"]
    assert {u["role"] for u in users} == ROLES
    assert len(users) == 8 and {u["organization"] for u in users} == {"Acme Retail", "Birch Studio"}

    # a cross-site page cannot sign a visitor in; an unknown email is the same 404 as demo-off
    evil = await web.post(
        "/api/v1/auth/demo/login", origin="https://evil.test", json={"email": "admin@acme.demo"}
    )
    assert evil.status_code == 403
    assert (
        await web.post("/api/v1/auth/demo/login", json={"email": "nobody@acme.demo"})
    ).status_code == 404
    ok = await web.post("/api/v1/auth/demo/login", json={"email": "lead@acme.demo"})
    assert ok.status_code == 200 and ok.json()["role"] == "team_lead"
    assert (await web.client.get("/api/v1/auth/session")).json()["organization_id"] == str(ACME)


async def test_demo_never_lists_or_signs_in_a_platform_admin(seeded, web, monkeypatch):
    # catches: demo login minting a session for an email that carries platform powers
    monkeypatch.setattr(settings, "demo_mode", True)
    monkeypatch.setattr(settings, "platform_admin_emails", "admin@acme.demo")
    emails = {u["email"] for u in (await web.client.get("/api/v1/auth/demo/users")).json()["users"]}
    assert "admin@acme.demo" not in emails and "admin@birch.demo" in emails
    assert (
        await web.post("/api/v1/auth/demo/login", json={"email": "admin@acme.demo"})
    ).status_code == 404


async def test_demo_users_lists_only_seeded_users(web, owner_engine, monkeypatch):
    monkeypatch.setattr(settings, "demo_mode", True)
    await _wipe(owner_engine)
    assert (await web.client.get("/api/v1/auth/demo/users")).json() == {"users": []}


async def test_reseed_converges_a_plan_left_behind_by_an_interrupted_run(
    seeded, app_engine, owner_engine
):
    # catches: org created but never upgraded (a crash between the two steps), then every re-run failing
    async with owner_engine.begin() as c:
        await c.execute(
            text("delete from escalation_policies where organization_id = :o"), {"o": ACME}
        )
        await c.execute(
            text("update organizations set plan_key = 'free' where id = :o"), {"o": ACME}
        )
    again = await demo.seed(app_engine, CLOCK, KEYS)
    assert again.secrets == {}
    plan = await _one(owner_engine, "select plan_key from organizations where id = :o", o=ACME)
    assert plan == "pro"
    sql = "select levels from escalation_policies where organization_id = :o"
    assert len(await _one(owner_engine, sql, o=ACME)) == 3


async def test_failed_demo_sign_in_keeps_the_previous_session_and_leaves_no_row(
    seeded, web, owner_engine, monkeypatch
):
    # catches: a refused sign-in deleting the caller's session or leaving an orphan one
    monkeypatch.setattr(settings, "demo_mode", True)
    ok = await web.post("/api/v1/auth/demo/login", json={"email": "lead@acme.demo"})
    assert ok.status_code == 200
    async with owner_engine.begin() as c:
        await c.execute(
            text(
                "delete from memberships"
                " where user_id = (select id from users where email = 'member@acme.demo')"
            )
        )
    before = await _one(owner_engine, "select count(*) from web_sessions")
    refused = await web.post("/api/v1/auth/demo/login", json={"email": "member@acme.demo"})
    assert refused.status_code == 404
    assert await _one(owner_engine, "select count(*) from web_sessions") == before
    assert (await web.client.get("/api/v1/auth/session")).status_code == 200  # still the lead


async def test_demo_roster_is_rate_limited_and_the_email_bounded(seeded, web, monkeypatch):
    # catches: an unauthenticated read that fans out queries without a limit; an unbounded body field
    monkeypatch.setattr(settings, "demo_mode", True)
    monkeypatch.setattr(settings, "write_limit_per_principal", 2)
    codes = [(await web.client.get("/api/v1/auth/demo/users")).status_code for _ in range(3)]
    assert codes == [200, 200, 429]
    monkeypatch.setattr(settings, "write_limit_per_principal", 60)
    long = await web.post("/api/v1/auth/demo/login", json={"email": "a" * 300 + "@acme.demo"})
    assert long.status_code == 422

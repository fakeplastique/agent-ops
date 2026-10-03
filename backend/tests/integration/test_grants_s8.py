import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.db.session import tenant_session

from .helpers import make_world

pytestmark = pytest.mark.anyio

BASE = {
    "organization_id": ":o", "title": "'T'", "description": "''", "action_type": "'a'", "payload": "'{}'",
    "risk_level": "'low'", "effective_risk_level": "'low'", "content_hash": f"'{'a' * 64}'",
    "requested_by_session_id": ":s", "policy_snapshot": "'{}'", "voting_deadline_at": "now()",
}  # fmt: skip


async def insert(engine, w, **extra) -> bool:
    """True if app_rt may insert this row (BASE plus the extra columns)."""
    cols = BASE | extra
    sql = f"insert into decision_requests ({', '.join(cols)}) values ({', '.join(cols.values())})"
    try:
        async with tenant_session(engine, w.org) as s:
            await s.execute(text(sql), {"o": w.org, "s": w.session})
    except DBAPIError:
        return False
    return True


async def test_runtime_role_can_insert_a_plain_request(app_engine, owner_engine):
    assert await insert(app_engine, await make_world(owner_engine))


@pytest.mark.parametrize(
    "extra",
    [
        {"status": "'approved'"},
        {"version": "7"},
        {"receipt": '\'{"sig": "x"}\''},
        {"decided_at": "now()"},
        {"escalation_reason": "'x'"},
    ],
    ids=lambda e: next(iter(e)),
)
async def test_runtime_role_cannot_forge_lifecycle_columns_on_insert(
    app_engine, owner_engine, extra
):
    # catches: a request inserted already approved, with a forged receipt
    assert not await insert(app_engine, await make_world(owner_engine), **extra)


async def test_effective_risk_below_declared_is_refused_by_the_database(app_engine, owner_engine):
    w = await make_world(owner_engine)
    assert not await insert(app_engine, w, risk_level="'high'", effective_risk_level="'low'")
    assert not await insert(app_engine, w, risk_level="'medium'", effective_risk_level="'low'")
    assert await insert(app_engine, w, risk_level="'low'", effective_risk_level="'high'")


async def test_agent_session_can_only_touch_last_seen(app_engine, owner_engine):
    w = await make_world(owner_engine)

    async def update(sql):
        try:
            async with tenant_session(app_engine, w.org) as s:
                await s.execute(text(sql), {"s": w.session, "i": w.identity})
        except DBAPIError:
            return False
        return True

    assert await update("update agent_sessions set last_seen_at = now() where id = :s")
    # catches: moving a session (and the requests it made) onto another agent
    assert not await update("update agent_sessions set agent_identity_id = :i where id = :s")
    assert not await update("update agent_sessions set session_label = 'x' where id = :s")

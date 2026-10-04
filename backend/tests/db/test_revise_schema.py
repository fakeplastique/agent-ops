import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from tests.integration.helpers import approval_world, cast, fetch, make_world, open_request, voters

pytestmark = pytest.mark.anyio


async def refused(owner, sql, **p) -> bool:
    try:
        async with owner.begin() as c:
            await c.execute(text(sql), p)
    except DBAPIError:
        return True
    return False


async def test_a_decided_request_keeps_its_content_and_version(app_engine, owner_engine):
    w = await make_world(owner_engine, floors={"refund": "low"})
    req = await open_request(app_engine, w)
    for s in await voters(owner_engine, w, 3):
        await cast(app_engine, w, req, s)
    for sql in (
        "update decision_requests set title='x' where id=:r",
        "update decision_requests set payload='{}' where id=:r",
        "update decision_requests set content_hash=repeat('a', 64) where id=:r",
        "update decision_requests set version=2 where id=:r",
        "update decision_requests set risk_level='high', effective_risk_level='high' where id=:r",
    ):
        assert await refused(owner_engine, sql, r=req["id"]), (
            sql
        )  # catches: a receipt outliving a changed request (I4, I12)
    assert not await refused(
        owner_engine, "update decision_requests set updated_at=now() where id=:r", r=req["id"]
    )


async def test_version_only_moves_up_by_one(app_engine, owner_engine):
    w = await make_world(owner_engine)
    req = await open_request(app_engine, w)
    assert await refused(
        owner_engine, "update decision_requests set version=3 where id=:r", r=req["id"]
    )
    assert await refused(
        owner_engine, "update decision_requests set version=0 where id=:r", r=req["id"]
    )
    assert not await refused(
        owner_engine, "update decision_requests set version=2 where id=:r", r=req["id"]
    )
    assert await refused(
        owner_engine, "update decision_requests set version=1 where id=:r", r=req["id"]
    )


async def test_messages_constraints_and_grants(app_engine, owner_engine):
    from app.db.session import tenant_session

    w, req, _ = await approval_world(owner_engine, app_engine)
    base = "insert into messages (organization_id, decision_request_id, request_version, author_type, author_id, body) values (:o, :r, 1, :t, :a, :b)"
    ok = {"o": w.org, "r": req["id"], "t": "system", "a": None, "b": "hi"}
    for change in (
        {"t": "robot"},
        {"b": ""},
        {"b": "x" * 4097},
        {"a": w.identity},
        {"t": "agent", "a": None},
    ):
        assert await refused(owner_engine, base, **ok | change), change
    assert not await refused(owner_engine, base, **ok)

    async def as_app(sql, **p) -> bool:
        try:
            async with tenant_session(app_engine, w.org) as s:
                await s.execute(text(sql), p)
        except DBAPIError:
            return False
        return True

    assert not await as_app(
        "update messages set body='edited' where organization_id=:o", o=w.org
    )  # catches: editing the thread
    assert not await as_app("delete from messages where organization_id=:o", o=w.org)
    assert await as_app(base, **ok)
    assert (
        await fetch(
            owner_engine,
            "select relrowsecurity, relforcerowsecurity from pg_class where relname='messages'",
        )
    ) == [(True, True)]


async def test_content_of_a_pending_request_changes_only_with_a_version_bump_and_a_new_hash(
    app_engine, owner_engine
):
    w = await make_world(owner_engine)
    req = await open_request(app_engine, w)
    r = req["id"]
    new_hash = "b" * 64
    # catches: content and its hash drifting apart without going through revise
    assert await refused(owner_engine, "update decision_requests set title='x' where id=:r", r=r)
    assert await refused(owner_engine, "update decision_requests set payload='{}' where id=:r", r=r)
    assert await refused(
        owner_engine,
        "update decision_requests set risk_level='high', effective_risk_level='high' where id=:r",
        r=r,
    )
    assert await refused(
        owner_engine, "update decision_requests set description='x' where id=:r", r=r
    )
    assert await refused(
        owner_engine, "update decision_requests set policy_snapshot='{}' where id=:r", r=r
    )
    assert await refused(
        owner_engine, "update decision_requests set content_hash=:h where id=:r", r=r, h=new_hash
    )
    # a bump with a hash-bound change must also change the hash
    assert await refused(
        owner_engine, "update decision_requests set title='x', version=2 where id=:r", r=r
    )
    # a bump alone, a description-only revise (the hash does not cover it), and a full revise are fine
    assert not await refused(
        owner_engine, "update decision_requests set description='x', version=2 where id=:r", r=r
    )
    full = "update decision_requests set title='y', content_hash=:h, version=3 where id=:r"
    assert not await refused(owner_engine, full, r=r, h=new_hash)
    # timer and status moves do not touch content
    assert not await refused(
        owner_engine,
        "update decision_requests set voting_deadline_at = now() + interval '1 hour', updated_at=now() where id=:r",
        r=r,
    )

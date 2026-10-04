import asyncio
from datetime import timedelta

import pytest
from sqlalchemy import text

from app.db.session import tenant_session
from app.domain.errors import InvalidInput, NotFoundError, SessionLimit
from app.services.sessions import get_or_create_session

from .helpers import NOW, FakeClock, fetch, make_world

pytestmark = pytest.mark.anyio


async def sess(engine, w, header=None, argument=None, clock=NOW, identity=None):
    async with tenant_session(engine, w.org) as s:
        return await get_or_create_session(
            s, w.org, identity or w.identity, clock, header_label=header, argument_label=argument
        )


async def test_session_label_from_mcp_header_else_argument_get_or_create(app_engine, owner_engine):
    w = await make_world(owner_engine)
    first, created = await sess(app_engine, w, header="mcp-1", argument="ignored")
    again, created2 = await sess(app_engine, w, header="mcp-1")
    assert (
        created and not created2 and again == first
    )  # catches: a new session per call inflating the quorum
    by_arg, created3 = await sess(app_engine, w, argument="chat-7")
    assert created3 and by_arg != first
    assert (await sess(app_engine, w, argument="chat-7"))[0] == by_arg
    labels = {
        r[0]
        for r in await fetch(
            owner_engine,
            "select session_label from agent_sessions where agent_identity_id=:i",
            i=w.identity,
        )
    }
    assert {
        "mcp-1",
        "chat-7",
    } <= labels and "ignored" not in labels  # the header wins over the argument


async def test_concurrent_calls_with_one_label_make_one_session(app_engine, owner_engine):
    w = await make_world(owner_engine)
    results = await asyncio.gather(*(sess(app_engine, w, header="same") for _ in range(6)))
    assert len({r[0] for r in results}) == 1 and sum(1 for r in results if r[1]) == 1


async def test_max_10_new_sessions_per_identity_per_hour(app_engine, owner_engine):
    w = await make_world(owner_engine)
    base = len(
        await fetch(
            owner_engine, "select 1 from agent_sessions where agent_identity_id=:i", i=w.identity
        )
    )  # the fixture's own
    for i in range(10 - base):
        await sess(app_engine, w, header=f"s{i}")
    with pytest.raises(SessionLimit):  # catches: session farming to fill a quorum
        await sess(app_engine, w, header="one-too-many")
    assert (await sess(app_engine, w, header="s0"))[
        1
    ] is False  # an existing label is not a new session
    later = FakeClock(NOW.now() + timedelta(hours=1, seconds=1))
    assert (await sess(app_engine, w, header="fresh-hour", clock=later))[1] is True


async def test_the_session_cap_holds_under_concurrency(app_engine, owner_engine):
    w = await make_world(owner_engine)
    base = len(
        await fetch(
            owner_engine, "select 1 from agent_sessions where agent_identity_id=:i", i=w.identity
        )
    )
    results = await asyncio.gather(
        *(sess(app_engine, w, header=f"c{i}") for i in range(14)), return_exceptions=True
    )
    total = len(
        await fetch(
            owner_engine, "select 1 from agent_sessions where agent_identity_id=:i", i=w.identity
        )
    )
    ok = [r for r in results if not isinstance(r, BaseException)]
    limited = [r for r in results if isinstance(r, SessionLimit)]
    assert (
        total == 10 and len(ok) == 10 - base and len(ok) + len(limited) == 14
    )  # no race past the cap


async def test_the_cap_is_per_identity(app_engine, owner_engine):
    from .helpers import add_agent

    w = await make_world(owner_engine)
    for i in range(10):
        try:
            await sess(app_engine, w, header=f"a{i}")
        except SessionLimit:
            break
    other, _ = await add_agent(owner_engine, w)
    assert (await sess(app_engine, w, header="mine", identity=other))[1] is True


@pytest.mark.parametrize("label", ["", "x" * 129, "a\x00b", "\ud800", None])
async def test_a_bad_or_missing_label_is_invalid(app_engine, owner_engine, label):
    w = await make_world(owner_engine)
    with pytest.raises(InvalidInput):
        await sess(app_engine, w, header=label)


async def test_another_tenants_identity_is_not_found(app_engine, owner_engine):
    a, b = await make_world(owner_engine), await make_world(owner_engine)
    with pytest.raises(NotFoundError):
        await sess(app_engine, b, header="x", identity=a.identity)
    assert (
        await fetch(
            owner_engine,
            "select 1 from agent_sessions where organization_id=:o and session_label='x'",
            o=b.org,
        )
        == []
    )


async def test_a_revoked_identity_gets_no_session(app_engine, owner_engine):
    w = await make_world(owner_engine)
    async with owner_engine.begin() as c:
        await c.execute(
            text("update agent_identities set status='revoked' where id=:i"), {"i": w.identity}
        )
    with pytest.raises(NotFoundError):
        await sess(app_engine, w, header="x")

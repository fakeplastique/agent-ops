import json

import pytest

from app.mcp.tools import read
from tests.api.harness import agent_auth, user_auth
from tests.integration.helpers import add_agent, cast, fetch, make_world, open_request, staff

from .test_transport import READ_TOOLS

pytestmark = pytest.mark.anyio


async def call(c, name: str, **args) -> dict:
    r = await c.call_tool(name, args)
    assert not r.is_error, r.content
    return r.structured_content


async def test_get_decision_blind_until_own_vote_over_mcp(mcp, owner_engine, app_engine):
    w = await make_world(owner_engine)
    req = await open_request(app_engine, w)
    (_, first), (me, mine), (_, third) = [await add_agent(owner_engine, w) for _ in range(3)]
    user = await staff(owner_engine, w.org, "manager")
    await cast(app_engine, w, req, first, rationale="ship it, secret reasoning")
    rid = str(req["id"])
    async with (
        mcp.client(await agent_auth(app_engine, w.org, me)) as c,
        mcp.client(await user_auth(app_engine, w.org, user)) as human,
    ):
        before = await call(c, "get_decision", decision_id=rid, session_label="l")
        # catches: the MCP path skipping the blindness projection (SC8): only the count before voting
        assert before["votes"]["cast"] == 1 and before["votes"]["votes"] == []
        assert "secret reasoning" not in json.dumps(before)
        seen_by_human = await call(human, "get_decision", decision_id=rid)
        assert seen_by_human["votes"]["votes"] == [] and seen_by_human["votes"]["cast"] == 1
        await cast(app_engine, w, req, mine, verdict="reject")
        own = await call(c, "get_decision", decision_id=rid, session_label="l")
        # its own vote, still not the other one: round 1 is open
        assert [v["session_id"] for v in own["votes"]["votes"]] == [str(mine)]
        await cast(app_engine, w, req, third)
        after = await call(c, "get_decision", decision_id=rid, session_label="l")
        assert len(after["votes"]["votes"]) == 3  # round closed: revealed to everyone
        assert after["decision"]["id"] == rid and after["decision"]["title"] == req["title"]


async def test_get_decision_result_wait_seconds_clamped_to_30(
    mcp, owner_engine, app_engine, monkeypatch
):
    w = await make_world(owner_engine)
    req = await open_request(app_engine, w)
    slept, open_tx = [], []

    async def fake_sleep(seconds: float) -> None:
        slept.append(seconds)
        rows = await fetch(
            owner_engine,
            "select count(*) from pg_stat_activity where usename = 'app_rt'"
            " and state like 'idle in transaction%'",
        )
        open_tx.append(rows[0][0])

    monkeypatch.setattr(read, "_sleep", fake_sleep)
    async with mcp.client(await agent_auth(app_engine, w.org, w.identity)) as c:
        out = await call(c, "get_decision_result", decision_id=str(req["id"]), wait_seconds=1000)
        assert out["final"] is False and out["status"] == "pending_votes"
        # catches: an unbounded long-poll holding a worker
        assert sum(slept) == 30
        # catches: a DB transaction held open across the wait
        assert set(open_tx) == {0}
        slept.clear()
        await call(c, "get_decision_result", decision_id=str(req["id"]))
        assert slept == []  # default: answer now


async def test_pagination_skip_limit_nextlink_and_read_only_annotations(
    mcp, owner_engine, app_engine
):
    w = await make_world(owner_engine)
    ids = {str((await open_request(app_engine, w, title=f"r{i}"))["id"]) for i in range(3)}
    async with mcp.client(await agent_auth(app_engine, w.org, w.identity)) as c:
        tools = {t.name: t for t in (await c.list_tools()).tools}
        for name in READ_TOOLS:
            a = tools[name].annotations
            # catches: a read tool a client would treat as able to change things
            assert a is not None and a.read_only_hint is True and a.destructive_hint is False, name
        for name in ("list_open_decisions", "list_my_decisions"):
            first = await call(c, name, limit=2)
            assert (first["total"], first["skip"], first["limit"]) == (3, 0, 2)
            assert first["nextLink"] == f"{name}?skip=2&limit=2"
            rest = await call(c, name, skip=2, limit=2)
            assert rest["nextLink"] is None
            assert {d["id"] for d in first["items"] + rest["items"]} == ids
        thread = await call(c, "get_discussion", decision_id=next(iter(ids)), limit=1)
        assert {"items", "total", "skip", "limit", "nextLink"} <= thread.keys()
        too_big = await c.call_tool("list_open_decisions", {"limit": 101})
        assert too_big.is_error  # catches: an unpaged list (limit capped at 100 like REST)


async def test_get_policy_effective_for_action_type_and_risk(mcp, owner_engine, app_engine):
    w = await make_world(owner_engine, quorum=2, floors={"refund": "low", "deploy": "high"})
    async with mcp.client(await agent_auth(app_engine, w.org, w.identity)) as c:
        low = await call(c, "get_policy", action_type="refund", risk_level="low")
        high = await call(c, "get_policy", action_type="deploy", risk_level="low")
        unlisted = await call(c, "get_policy", action_type="other", risk_level="low")
        bad = await c.call_tool("get_policy", {"action_type": "Refund!", "risk_level": "low"})
    assert low["policy"]["quorum_size"] == 2
    assert (low["effective_risk_level"], low["auto_approve_possible"]) == ("low", True)
    assert (high["effective_risk_level"], high["auto_approve_possible"]) == ("high", False)
    assert unlisted["effective_risk_level"] == "medium"  # an unlisted action type floors at medium
    assert bad.is_error and "action_type" in bad.content[0].text  # says what to fix


async def test_bad_session_label_is_an_actionable_tool_error(mcp, owner_engine, app_engine):
    w = await make_world(owner_engine)
    req = await open_request(app_engine, w)
    async with mcp.client(await agent_auth(app_engine, w.org, w.identity)) as c:
        r = await c.call_tool(
            "get_decision", {"decision_id": str(req["id"]), "session_label": "a\x00b"}
        )
    # catches: a NUL reaching Postgres and coming back as an opaque "Error executing tool"
    assert r.is_error and "session label" in r.content[0].text

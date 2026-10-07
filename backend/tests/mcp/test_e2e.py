"""Scripted agents and a human over a real MCP client, each with its own token and session label."""

import json

import pytest

from app.config import settings
from app.domain.receipt import KeyRing
from app.services.receipts import verify_receipt
from tests.api.harness import agent_auth, user_auth
from tests.integration.helpers import add_agent, make_world, staff

from .test_write_tools import REQ, call, create, fails

pytestmark = pytest.mark.anyio
KEYS = KeyRing.parse(settings.receipt_keys)


async def _vote(c, rid: str, label: str, verdict: str = "approve", **over) -> dict:
    args = {"decision_id": rid, "request_version": 1, "verdict": verdict, "confidence": 0.9}
    return await call(c, "cast_vote", **(args | over), session_label=label)


async def test_three_agents_vote_low_risk_auto_approves_and_receipt_verifies(
    mcp, owner_engine, app_engine
):
    w = await make_world(owner_engine, floors={"refund": "low"})
    reviewers = [(await add_agent(owner_engine, w))[0] for _ in range(3)]
    async with mcp.client(await agent_auth(app_engine, w.org, w.identity)) as requester:
        made = await create(requester, "req-1")
        rid = made["id"]
        assert (made["status"], made["effective_risk_level"]) == ("pending_votes", "low")
        last = None
        for i, ident in enumerate(reviewers):
            async with mcp.client(await agent_auth(app_engine, w.org, ident)) as c:
                last = await _vote(c, rid, f"reviewer-{i}")
        # catches: a wiring gap between the MCP tools and the decision engine
        assert last is not None and (last["cast"], last["status"]) == (3, "approved")
        out = await call(requester, "get_decision_result", decision_id=rid)
    assert out["final"] is True and out["status"] == "approved"
    assert len(out["votes"]["votes"]) == 3  # revealed once the round closed
    assert await verify_receipt(app_engine, KEYS, out["receipt"]) is True


async def test_split_votes_then_human_approves_via_user_token(mcp, owner_engine, app_engine):
    w = await make_world(owner_engine, floors={"refund": "low"})
    reviewers = [(await add_agent(owner_engine, w))[0] for _ in range(3)]
    manager = await staff(owner_engine, w.org, "manager")
    async with mcp.client(await agent_auth(app_engine, w.org, w.identity)) as requester:
        rid, last = (await create(requester, "req-1"))["id"], {}
        for i, (ident, verdict) in enumerate(zip(reviewers, ("approve", "approve", "reject"))):
            async with mcp.client(await agent_auth(app_engine, w.org, ident)) as c:
                last = await _vote(c, rid, f"reviewer-{i}", verdict)
        assert last["status"] == "pending_approval"  # disagreement: humans decide
    async with mcp.client(await user_auth(app_engine, w.org, manager)) as human:
        args = {"decision_id": rid, "decision": "approve", "comment": "checked"}
        text = await fails(human, "decide_approval_step", **args, request_version=2)
        assert "stale version" in text
        out = await call(human, "decide_approval_step", **args, request_version=1)
        # catches: the personal access token path to deciding broken over MCP
        assert (out["status"], out["request_status"], out["next_step_id"]) == (
            "approved",
            "approved",
            None,
        )
        result = await call(human, "get_decision_result", decision_id=rid)
        # one decision per user and version (I8): a second call finds nothing waiting
        assert "get_decision_result" in await fails(
            human, "decide_approval_step", **args, request_version=1
        )
    assert result["status"] == "approved"
    assert await verify_receipt(app_engine, KEYS, result["receipt"]) is True


SECRET, CONFIDENCE = "SECRET-RATIONALE-41d7", 0.3712


def _args(name: str, rid: str, label: str) -> dict:
    """A plausible call of every tool against the request under test."""
    by_id = {"decision_id": rid}
    return {
        "list_open_decisions": {},
        "list_my_decisions": {},
        "get_decision": by_id | {"session_label": label},
        "get_discussion": by_id,
        "get_decision_result": by_id | {"session_label": label},
        "get_policy": {"action_type": "refund", "risk_level": "low"},
        "create_decision_request": REQ | {"session_label": label},
        "cast_vote": by_id | {"request_version": 1, "verdict": "approve", "confidence": 0.5,
                              "session_label": label},
        "post_message": by_id | {"body": "hello", "session_label": label},
        "decide_approval_step": by_id | {"request_version": 1, "decision": "approve"},
        "revise_decision_request": by_id | {"title": "new title", "session_label": label},
        "withdraw_decision_request": by_id | {"session_label": label},
    }[name]  # fmt: skip


LAST = ("revise_decision_request", "withdraw_decision_request")  # they end the round: walked last


async def test_no_blind_verdict_in_any_tool_output_before_reveal(mcp, owner_engine, app_engine):
    w = await make_world(owner_engine, floors={"refund": "low"})
    (voter, _), (other, _) = [await add_agent(owner_engine, w) for _ in range(2)]
    manager = await staff(owner_engine, w.org, "manager")
    async with mcp.client(await agent_auth(app_engine, w.org, w.identity)) as requester:
        rid = (await create(requester, "req-1"))["id"]
        async with mcp.client(await agent_auth(app_engine, w.org, voter)) as c:
            await _vote(c, rid, "v1", "reject", confidence=CONFIDENCE, rationale=SECRET)
        seen: list[tuple[str, str, str]] = []
        names: set[str] = set()

        async def walk(c, who: str, label: str, tools) -> None:
            for name in tools:
                r = await c.call_tool(name, _args(name, rid, label))
                seen.append((who, name, json.dumps(r.structured_content) + str(r.content)))
            seen.append((who, "resource", str(await c.read_resource(f"decision://{rid}"))))

        async with (
            mcp.client(await agent_auth(app_engine, w.org, other)) as peer,
            mcp.client(await user_auth(app_engine, w.org, manager)) as human,
            mcp.client(await user_auth(app_engine, w.org, w.owner_user)) as owner,
        ):
            clients = [(requester, "requester", "req-1"), (peer, "peer", "p1"),
                       (human, "manager", "-"), (owner, "owner", "-")]  # fmt: skip
            for c, who, label in clients:
                listed = [t.name for t in (await c.list_tools()).tools]
                names |= set(listed)
                await walk(c, who, label, [n for n in listed if n not in LAST])
            await walk(peer, "peer", "p1", ["decide_approval_step"])  # hidden, still called
            await walk(requester, "requester", "req-1", LAST)
    assert names == set(_TOOLS)  # catches: a tool added later and never walked
    for who, name, text in seen:
        # catches: a verdict, confidence or rationale leaking through a tool nobody checked (I5, SC8)
        assert SECRET not in text and str(CONFIDENCE) not in text, (who, name)


_TOOLS = [
    "list_open_decisions", "list_my_decisions", "get_decision", "get_discussion",
    "get_decision_result", "get_policy", "create_decision_request", "cast_vote", "post_message",
    "decide_approval_step", "revise_decision_request", "withdraw_decision_request",
]  # fmt: skip

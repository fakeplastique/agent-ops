"""FM2: tenant B calls every MCP tool and the decision resource with tenant A's ids."""

import uuid

import pytest
from mcp.shared.exceptions import MCPError

from tests.api.harness import agent_auth, user_auth
from tests.integration.helpers import approval_world, fetch, make_world, open_request, staff

pytestmark = pytest.mark.anyio

# Every tool: how B probes A. "id" tools must answer exactly like an id that exists nowhere; "list" tools must
# not show A's request; "policy" must give B's own policy; "create" must land in B. A tool not here fails.
TOOLS: dict[str, tuple[str, dict]] = {
    "get_decision": ("id", {"session_label": "l"}),
    "get_discussion": ("id", {}),
    "get_decision_result": ("id", {}),
    "list_open_decisions": ("list", {}),
    "list_my_decisions": ("list", {}),
    "get_policy": ("policy", {"action_type": "refund", "risk_level": "low"}),
    "create_decision_request": ("create", {}),
    "cast_vote": ("id", {"request_version": 1, "verdict": "approve", "confidence": 0.9,
                         "session_label": "l"}),
    "post_message": ("id", {"body": "hi", "session_label": "l"}),
    "revise_decision_request": ("id", {"title": "B was here", "session_label": "l"}),
    "withdraw_decision_request": ("id", {"session_label": "l"}),
    "decide_approval_step": ("id", {"request_version": 1, "decision": "approve"}),
}  # fmt: skip
# Tools a principal kind may not use at all: B gets the same refusal for A's id and for a missing one.
REFUSED = {
    "agent": {"decide_approval_step"},
    "user": {"cast_vote", "revise_decision_request", "withdraw_decision_request"},
}
CREATE = {"title": "B", "action_type": "refund", "payload": {}, "risk_level": "low",
          "session_label": "l"}  # fmt: skip


async def _probe(c, name: str, a_id: str) -> tuple[bool, str]:
    kind, args = TOOLS[name]
    if kind == "id":
        args = args | {"decision_id": a_id}
    r = await c.call_tool(name, args)
    return r.is_error, r.content[0].text if r.content else ""


async def test_every_tool_is_in_the_table(mcp, owner_engine, app_engine):
    w = await make_world(owner_engine)
    user = await staff(owner_engine, w.org, "manager")
    async with mcp.client(await agent_auth(app_engine, w.org, w.identity)) as c:
        agent = {t.name for t in (await c.list_tools()).tools}
    async with mcp.client(await user_auth(app_engine, w.org, user)) as c:
        human = {t.name for t in (await c.list_tools()).tools}
    assert agent | human == TOOLS.keys()  # catches: a new tool nobody probed across tenants
    assert agent == TOOLS.keys() - REFUSED["agent"]  # the hidden tool is still probed by agents


async def _probe_all(mcp, app_engine, b, b_user, a_id: str) -> None:
    nowhere = str(uuid.uuid4())
    for who, headers in (
        ("agent", await agent_auth(app_engine, b.org, b.identity)),
        ("user", await user_auth(app_engine, b.org, b_user)),
    ):
        async with mcp.client(headers) as c:
            for name, (kind, _) in TOOLS.items():
                if kind == "create":
                    r = await c.call_tool(name, CREATE)
                    assert not r.is_error and r.structured_content["id"] != a_id, name
                    continue
                err, text = await _probe(c, name, a_id)
                if kind == "id":
                    # catches: tenant B reading or changing A's request by id, or learning that it exists
                    assert err and (err, text) == await _probe(c, name, nowhere), name
                    assert name in REFUSED[who] or "not found" in text, (who, name, text)
                elif kind == "list":
                    r = await c.call_tool(name, {})
                    assert not r.is_error and a_id not in str(r.structured_content), name
                else:
                    r = await c.call_tool(name, TOOLS[name][1])
                    assert r.structured_content["policy"]["quorum_size"] == 3, name
            res, missing = await _read(c, a_id), await _read(c, nowhere)
            assert res.startswith("error") and res == missing  # the resource too (same rule)


async def test_cross_tenant_read_tools_return_not_found(mcp, owner_engine, app_engine):
    a = await make_world(owner_engine, quorum=2)
    b = await make_world(owner_engine)
    a_req = await open_request(app_engine, a)
    b_user = await staff(owner_engine, b.org, "org_admin")
    await _probe_all(mcp, app_engine, b, b_user, str(a_req["id"]))


async def test_cross_tenant_write_tools_all_not_found(mcp, owner_engine, app_engine):
    # A's request waits for a human: every write would succeed for A's own principals right now.
    _, a_req, _ = await approval_world(owner_engine, app_engine)
    b = await make_world(owner_engine)
    b_user = await staff(owner_engine, b.org, "org_admin")
    before = await _state(owner_engine, a_req["id"])
    await _probe_all(mcp, app_engine, b, b_user, str(a_req["id"]))
    # catches: a write that answered "not found" after changing A's data anyway
    assert await _state(owner_engine, a_req["id"]) == before


async def _state(owner, rid) -> list:
    return await fetch(
        owner,
        "select r.status, r.version, r.title, (select count(*) from votes where decision_request_id = r.id),"
        " (select count(*) from messages where decision_request_id = r.id),"
        " (select string_agg(status, ',') from approval_steps where decision_request_id = r.id)"
        " from decision_requests r where r.id = :r",
        r=rid,
    )


async def _read(c, rid: str) -> str:
    try:
        return str(await c.read_resource(f"decision://{rid}"))
    except MCPError as e:  # the client raises the server's resource error
        return f"error {e}".replace(rid, "<id>")

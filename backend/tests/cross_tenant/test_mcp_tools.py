"""FM2 (read half): tenant B calls every MCP tool and the decision resource with tenant A's ids."""

import uuid

import pytest
from mcp.shared.exceptions import MCPError

from tests.api.harness import agent_auth, user_auth
from tests.integration.helpers import make_world, open_request, staff

pytestmark = pytest.mark.anyio

# Every tool: how B probes A. "id" tools must answer exactly like an id that exists nowhere; "list" tools must
# not show A's request; "policy" must give B's own policy. S24 adds the write tools; a tool not here fails.
TOOLS: dict[str, tuple[str, dict]] = {
    "get_decision": ("id", {"session_label": "l"}),
    "get_discussion": ("id", {}),
    "get_decision_result": ("id", {}),
    "list_open_decisions": ("list", {}),
    "list_my_decisions": ("list", {}),
    "get_policy": ("policy", {"action_type": "refund", "risk_level": "low"}),
}


async def _probe(c, name: str, a_id: str) -> tuple[bool, str]:
    kind, args = TOOLS[name]
    if kind == "id":
        args = args | {"decision_id": a_id}
    r = await c.call_tool(name, args)
    return r.is_error, r.content[0].text if r.content else ""


async def test_every_tool_is_in_the_table(mcp, owner_engine, app_engine):
    w = await make_world(owner_engine)
    async with mcp.client(await agent_auth(app_engine, w.org, w.identity)) as c:
        names = {t.name for t in (await c.list_tools()).tools}
    assert names == TOOLS.keys()  # catches: a new tool nobody probed across tenants


async def test_cross_tenant_read_tools_return_not_found(mcp, owner_engine, app_engine):
    a = await make_world(owner_engine, quorum=2)
    b = await make_world(owner_engine)
    a_req = await open_request(app_engine, a)
    a_id, nowhere = str(a_req["id"]), str(uuid.uuid4())
    b_user = await staff(owner_engine, b.org, "org_admin")
    for headers in (
        await agent_auth(app_engine, b.org, b.identity),
        await user_auth(app_engine, b.org, b_user),
    ):
        async with mcp.client(headers) as c:
            for name, (kind, _) in TOOLS.items():
                err, text = await _probe(c, name, a_id)
                if kind == "id":
                    # catches: tenant B reading A's request by id, or learning that it exists
                    assert err and "not found" in text, name
                    assert (err, text) == await _probe(c, name, nowhere), name
                elif kind == "list":
                    r = await c.call_tool(name, {})
                    assert not r.is_error and a_id not in str(r.structured_content), name
                else:
                    r = await c.call_tool(name, TOOLS[name][1])
                    assert r.structured_content["policy"]["quorum_size"] == 3, name
            res, missing = await _read(c, a_id), await _read(c, nowhere)
            assert res.startswith("error") and res == missing  # the resource too (same rule)


async def _read(c, rid: str) -> str:
    try:
        return str(await c.read_resource(f"decision://{rid}"))
    except MCPError as e:  # the client raises the server's resource error
        return f"error {e}".replace(rid, "<id>")

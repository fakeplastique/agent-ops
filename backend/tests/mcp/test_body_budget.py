"""S32 review C2: the 14 MB body is for an authenticated add_attachment call only; every other /mcp request keeps 1 MB."""

import base64
import json

import pytest

from tests.api.harness import agent_auth
from tests.integration.helpers import make_world, open_request
from tests.mcp.conftest import mcp  # noqa: F401 (the fixture)

pytestmark = pytest.mark.anyio

HEADERS = {"accept": "application/json, text/event-stream", "content-type": "application/json"}
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * (2 * 1024 * 1024)  # 2 MB: over the 1 MB default


def rpc(name: str, arguments: dict) -> bytes:
    body = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {"name": name, "arguments": arguments},
    }
    return json.dumps(body).encode()


async def post(mcp, headers, content: bytes):  # noqa: F811
    async with mcp.http(HEADERS | headers) as c:
        return await c.post("/mcp", content=content)


async def test_unauthenticated_2mb_mcp_body_is_refused(mcp):  # noqa: F811
    # catches: the large cap applying before authentication
    r = await post(mcp, {}, b"x" * (2 * 1024 * 1024))
    assert r.status_code == 413, r.status_code
    big = rpc(
        "add_attachment",
        {"decision_id": "x", "filename": "a.png", "content_base64": "A" * 2_000_000},
    )
    assert (await post(mcp, {}, big)).status_code == 413  # even shaped like the allowed call
    assert (await post(mcp, {}, b"{}")).status_code == 401  # a small one still gets the usual 401


async def test_other_tools_keep_the_1mb_cap_when_authenticated(mcp, owner_engine, app_engine):  # noqa: F811
    w = await make_world(owner_engine, "pro")
    h = await agent_auth(app_engine, w.org, w.identity)
    r = await post(mcp, h, rpc("post_message", {"decision_id": "x", "body": "A" * 2_000_000}))
    assert r.status_code == 413, r.status_code
    batch = json.dumps([json.loads(rpc("add_attachment", {}))]).encode() + b" " * 2_000_000
    assert (await post(mcp, h, batch)).status_code == 413  # a batch cannot smuggle the large cap


async def test_authenticated_add_attachment_may_exceed_1mb(mcp, owner_engine, app_engine):  # noqa: F811
    w = await make_world(owner_engine, "pro")
    req = await open_request(app_engine, w)
    h = await agent_auth(app_engine, w.org, w.identity)
    args = {
        "decision_id": str(req["id"]),
        "filename": "a.png",
        "content_base64": base64.b64encode(PNG).decode(),
    }
    r = await post(mcp, h, rpc("add_attachment", args))
    assert r.status_code == 200 and '"isError":true' not in r.text.replace(" ", ""), r.text[:300]

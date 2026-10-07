import pytest

from app.config import settings
from app.mcp.server import http_app
from tests.api.harness import agent_auth
from tests.integration.helpers import make_world

from .conftest import Mcp

pytestmark = pytest.mark.anyio

READ_TOOLS = {
    "list_open_decisions",
    "list_my_decisions",
    "get_decision",
    "get_discussion",
    "get_decision_result",
    "get_policy",
}
LIST = {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}


async def test_real_client_lists_tools_over_streamable_http_stateless(
    mcp, owner_engine, app_engine
):
    w = await make_world(owner_engine)
    headers = await agent_auth(app_engine, w.org, w.identity)
    async with mcp.client(headers) as c:
        tools = (await c.list_tools()).tools
    assert READ_TOOLS <= {t.name for t in tools}
    async with mcp.http(headers) as http:
        r = await http.post(
            "/mcp", json=LIST, headers={"Accept": "application/json, text/event-stream"}
        )
    # catches: a stateful server (sessions die with a replica; D12 labels sessions instead)
    assert r.status_code == 200 and "mcp-session-id" not in r.headers


async def test_public_hostname_allowlist_not_421(owner_engine, app_engine, monkeypatch):
    from app.api import deps

    monkeypatch.setattr(deps, "_engine", app_engine)
    monkeypatch.setattr(settings, "mcp_allowed_hosts", "agent-ops.up.railway.app")
    w = await make_world(owner_engine)
    headers = await agent_auth(app_engine, w.org, w.identity)
    accept = {"Accept": "application/json, text/event-stream"}
    app = http_app()
    async with app.router.lifespan_context(app):
        m = Mcp(app)
        async with m.http(headers, "https://agent-ops.up.railway.app") as http:
            ok = await http.post("/mcp", json=LIST, headers=accept)
        async with m.http(headers, "https://evil.example") as http:
            other = await http.post("/mcp", json=LIST, headers=accept)
    # catches: the deployed host refused by DNS-rebinding protection, or the protection switched off
    assert ok.status_code == 200, ok.text
    assert other.status_code == 421


async def test_main_app_serves_mcp_at_the_exact_path(owner_engine, app_engine, monkeypatch):
    import httpx2

    from app.api import deps
    from app.main import app

    monkeypatch.setattr(deps, "_engine", app_engine)
    w = await make_world(owner_engine)
    headers = await agent_auth(app_engine, w.org, w.identity)

    url = app_engine.url.render_as_string(hide_password=False)
    monkeypatch.setattr(settings, "database_url", url)  # the lifespan's role check, on the test DB
    async with app.router.lifespan_context(app):
        transport = httpx2.ASGITransport(app=app)  # type: ignore[arg-type]
        async with httpx2.AsyncClient(transport=transport, base_url="http://localhost") as http:
            anon = await http.post("/mcp", json=LIST)
            r = await http.post(
                "/mcp",
                json=LIST,
                headers=headers | {"Accept": "application/json, text/event-stream"},
            )
    # catches: /mcp not mounted, redirected to /mcp/, or the SDK's task group never started
    assert anon.status_code == 401 and anon.json()["error"] == "invalid_token"
    assert r.status_code == 200 and READ_TOOLS <= {t["name"] for t in r.json()["result"]["tools"]}


async def test_authenticated_get_is_405_not_an_open_stream(mcp, owner_engine, app_engine):
    import anyio

    w = await make_world(owner_engine)
    headers = await agent_auth(app_engine, w.org, w.identity)
    async with mcp.http(headers) as http:
        with anyio.fail_after(3):  # catches: an idle SSE stream held open per live token
            got = await http.get("/mcp", headers={"Accept": "text/event-stream"})
            deleted = await http.delete("/mcp")
        anon = await http.get("/mcp", headers={"Authorization": ""})
    assert (got.status_code, got.headers.get("allow")) == (405, "POST")
    assert deleted.status_code == 405
    assert anon.status_code == 401  # auth first: no method hint for a caller without a token
    async with mcp.client(headers) as c:  # the real client still works without the GET stream
        assert READ_TOOLS <= {t.name for t in (await c.list_tools()).tools}

import re
from pathlib import Path

import pytest

from app.db.session import tenant_session
from app.services.tokens import create_pat, issue_agent_token, revoke_agent, revoke_pat
from tests.integration.helpers import make_world, staff

from .test_transport import LIST

pytestmark = pytest.mark.anyio
ACCEPT = {"Accept": "application/json, text/event-stream"}
MCP_SRC = Path(__file__).parents[2] / "app" / "mcp"


async def test_bad_missing_revoked_token_is_clean_auth_error_no_existence_hint(
    mcp, owner_engine, app_engine
):
    w = await make_world(owner_engine)
    user = await staff(owner_engine, w.org, "member")
    async with tenant_session(app_engine, w.org) as s:
        agent = await issue_agent_token(s, w.org, w.identity)
        pat_id, pat = await create_pat(s, w.org, user, "ci")
    async with mcp.http({"Authorization": f"Bearer {agent}"}) as http:
        assert (await http.post("/mcp", json=LIST, headers=ACCEPT)).status_code == 200
    async with tenant_session(app_engine, w.org) as s:
        await revoke_pat(s, w.org, pat_id)
        await revoke_agent(s, w.org, w.identity)
    tries = [
        {},
        {"Authorization": "Basic x"},
        {"Authorization": "Bearer nope"},
        {"Authorization": "Bearer aop_" + "q" * 43},
        {"Authorization": f"Bearer {agent}"},  # revoked identity
        {"Authorization": f"Bearer {pat}"},  # revoked PAT
    ]
    answers = set()
    for headers in tries:
        async with mcp.http(headers) as http:
            r = await http.post("/mcp", json=LIST, headers=ACCEPT)
        assert r.status_code == 401
        # catches: a traceback or the token echoed back
        assert "Traceback" not in r.text and agent[4:12] not in r.text and pat[4:12] not in r.text
        answers.add((r.text, r.headers.get("www-authenticate")))
    assert len(answers) == 1  # catches: unknown vs revoked told apart (a token or account oracle)


def test_handlers_never_read_authorization_header():
    sources = {p.name: p.read_text() for p in MCP_SRC.rglob("*.py")}
    assert "read.py" in sources  # catches: a guard that passes because it scans nothing
    offenders = [
        name
        for name, src in sources.items()
        # catches: identity parsed outside the TokenVerifier (raw header, the HTTP request, or ctx headers)
        if re.search(
            r"authorization|\.headers|request_context\.request|ctx\.request", src, re.IGNORECASE
        )
    ]
    assert offenders == []

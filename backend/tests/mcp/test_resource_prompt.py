import json

import pytest

from tests.api.harness import agent_auth
from tests.integration.helpers import add_agent, cast, make_world, open_request

from .test_read_tools import call

pytestmark = pytest.mark.anyio


async def test_decision_resource_same_visibility_as_get_decision(mcp, owner_engine, app_engine):
    w = await make_world(owner_engine)
    req = await open_request(app_engine, w)
    (_, first), (me, _), (_, third) = [await add_agent(owner_engine, w) for _ in range(3)]
    await cast(app_engine, w, req, first, rationale="hidden until the round closes")
    rid = str(req["id"])

    async def resource(c) -> dict:
        got = await c.read_resource(f"decision://{rid}")
        return json.loads(got.contents[0].text)

    async with mcp.client(await agent_auth(app_engine, w.org, me)) as c:
        blind = await resource(c)
        # catches: the resource bypassing blindness (round 1 open: the count, no vote)
        assert blind == await call(c, "get_decision", decision_id=rid)
        assert blind["votes"]["cast"] == 1 and blind["votes"]["votes"] == []
        await cast(app_engine, w, req, (await add_agent(owner_engine, w))[1])
        await cast(app_engine, w, req, third)
        revealed = await resource(c)
        assert revealed == await call(c, "get_decision", decision_id=rid)
        assert len(revealed["votes"]["votes"]) == 3

        prompt = await c.get_prompt("review_decision", {"decision_id": rid})
        text = prompt.messages[0].content.text  # type: ignore[union-attr]
        assert rid in text and "blind" in text and "confidence" in text.lower()
        assert req["title"] not in text  # only the id goes into the prompt, never agent text

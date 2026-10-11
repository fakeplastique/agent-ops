import asyncio

import pytest

from tests.api.harness import user_auth
from tests.integration.helpers import make_org, staff
from tests.integration.web_harness import ORIGIN
from tests.integration.ws_harness import WS, ticket

pytestmark = pytest.mark.anyio


async def test_foreign_origin_rejected_before_upgrade(web, app_engine, owner_engine):
    org = await make_org(owner_engine)
    t = await ticket(
        web, await user_auth(app_engine, org, await staff(owner_engine, org, "member"))
    )
    for origin in (None, "", "null", "https://evil.test", "https://app.test.evil.test",
                   "https://app.test/", "HTTPS://APP.TEST", f"{ORIGIN}, https://evil.test"):  # fmt: skip
        sock = WS(origin=origin, ticket=t)
        # catches: cross-site WebSocket hijack (a page elsewhere riding the user's ticket)
        assert (await sock.open())["type"] == "websocket.close", origin
        assert sock.task is not None
        await asyncio.wait_for(sock.task, 5)  # the app returned: nothing held
    # Checked before the ticket is redeemed: a foreign page cannot even burn it.
    assert (await WS(ticket=t).open())["type"] == "websocket.accept"

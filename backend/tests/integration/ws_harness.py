"""An ASGI WebSocket client for app.main.app in the test's own loop (the shared engine and Redis stay usable).

`stall=True` makes every data frame hang in `send`, the way a client that stopped reading looks to the server."""

import asyncio
import json
from collections.abc import MutableMapping
from typing import Any
from urllib.parse import urlencode

from app.main import app
from tests.integration.web_harness import ORIGIN

TICKET = "/api/v1/realtime/ticket"


async def ticket(web, headers: dict[str, str] | None = None) -> str:
    # The web Origin for a cookie session; a bearer token ignores it.
    res = await web.client.post(TICKET, headers={"Origin": ORIGIN} | (headers or {}))
    assert res.status_code == 201, res.text
    return res.json()["ticket"]


class WS:
    def __init__(self, origin: str | None = ORIGIN, stall: bool = False, **query: Any) -> None:
        headers = [(b"host", b"api.test")]
        if origin is not None:
            headers.append((b"origin", origin.encode()))
        self.scope = {
            "type": "websocket", "asgi": {"version": "3.0"}, "scheme": "wss", "path": "/ws",
            "raw_path": b"/ws", "root_path": "", "query_string": urlencode(query).encode(),
            "headers": headers, "client": ("127.0.0.1", 50000), "server": ("api.test", 443),
            "subprotocols": [],
        }  # fmt: skip
        self.stall = stall
        self.to_app: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self.from_app: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self.task: asyncio.Task[None] | None = None

    async def _send(self, message: MutableMapping[str, Any]) -> None:
        if self.stall and message["type"] == "websocket.send":
            await asyncio.Event().wait()  # never read
        await self.from_app.put(dict(message))

    async def open(self) -> dict[str, Any]:
        """Start the handshake: the app's first message (`websocket.accept` or a refusing close)."""
        self.task = asyncio.create_task(app(self.scope, self.to_app.get, self._send))
        await self.to_app.put({"type": "websocket.connect"})
        return await asyncio.wait_for(self.from_app.get(), 5)

    async def frame(self, timeout: float = 5) -> dict[str, Any]:
        msg = await asyncio.wait_for(self.from_app.get(), timeout)
        assert msg["type"] == "websocket.send", msg
        return json.loads(msg["text"])

    async def closed(self, timeout: float = 5) -> dict[str, Any]:
        msg = await asyncio.wait_for(self.from_app.get(), timeout)
        assert msg["type"] == "websocket.close", msg
        return msg

    async def leave(self, timeout: float = 5) -> None:
        """The client goes away; the app must return."""
        await self.to_app.put({"type": "websocket.disconnect", "code": 1001})
        assert self.task is not None
        await asyncio.wait_for(self.task, timeout)

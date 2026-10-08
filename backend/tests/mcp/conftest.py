from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass

import httpx2
import pytest
from mcp import Client
from mcp.client.streamable_http import streamable_http_client

from app.api import deps
from app.mcp.server import http_app
from tests.conftest import TEST_REDIS

BASE = "http://localhost"


@dataclass
class Mcp:
    app: object

    def http(self, headers: dict[str, str] | None = None, base: str = BASE) -> httpx2.AsyncClient:
        return httpx2.AsyncClient(
            transport=httpx2.ASGITransport(app=self.app),  # type: ignore[arg-type]
            base_url=base,
            headers=headers,
        )

    @asynccontextmanager
    async def client(self, headers: dict[str, str]) -> AsyncIterator[Client]:
        """A real MCP client over Streamable HTTP; the response cache is off so every call hits the server."""
        async with (
            self.http(headers) as http,
            Client(streamable_http_client(f"{BASE}/mcp", http_client=http), cache=None) as c,
        ):
            yield c


@pytest.fixture
async def mcp(app_engine, monkeypatch) -> AsyncIterator[Mcp]:
    """The MCP endpoint as main.py serves it, on the test database and Redis db 15 (write limits)."""
    from redis.asyncio import Redis

    redis = Redis.from_url(TEST_REDIS)
    monkeypatch.setattr(deps, "_engine", app_engine)
    monkeypatch.setattr(deps, "_redis", redis)
    app = http_app()
    async with app.router.lifespan_context(app):
        yield Mcp(app)
    await redis.aclose()

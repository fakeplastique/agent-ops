import re
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response
from starlette.routing import Route
from starlette.types import Receive, Scope, Send

from app.adapters.cache_redis import RedisCache
from app.api import health, metrics
from app.api.errors import document_errors, register_error_handlers
from app.api.limits import BodyLimitMiddleware
from app.api.v1 import api_v1_router
from app.config import settings
from app.db.engine import make_engine
from app.db.guard import assert_safe_role
from app.mcp.server import http_app
from app.realtime import ws
from app.services import cache


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    # Fail closed: a role that bypasses RLS must never serve traffic.
    engine = make_engine(settings.database_url)
    try:
        await assert_safe_role(engine)
    finally:
        await engine.dispose()
    # The SDK runs an MCP app's task group once per instance, so every startup builds its own.
    app.state.mcp = mcp_app = http_app()
    store = RedisCache(settings.redis_url)
    cache.configure(
        store
    )  # REST and MCP share it: both reach flags and policies through the services
    try:
        async with mcp_app.router.lifespan_context(mcp_app):
            yield
    finally:
        cache.configure(None)
        await store.redis.aclose()


class McpEndpoint:
    """Hands /mcp to the MCP app this process's lifespan built."""

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        await scope["app"].state.mcp(scope, receive, send)


_REQUEST_ID = re.compile(r"[\w-]{1,64}")  # a client-sent id is echoed into logs: bound it

app = FastAPI(title="Agent Ops", lifespan=lifespan, openapi_url="/api/v1/openapi.json")
app.add_middleware(BodyLimitMiddleware)


@app.middleware("http")
async def request_id_middleware(request: Request, call_next) -> Response:
    given = request.headers.get("X-Request-ID", "")
    req_id = given if _REQUEST_ID.fullmatch(given) else str(uuid.uuid4())
    request.state.request_id = req_id
    response = await call_next(request)
    response.headers["X-Request-ID"] = req_id
    return response


register_error_handlers(app)
app.include_router(health.router)
app.include_router(metrics.router)
app.include_router(api_v1_router)
app.include_router(ws.router)  # /ws at the root (spec 5.3)
app.router.routes.append(
    Route("/mcp", McpEndpoint())
)  # an exact path: a Mount would redirect /mcp to /mcp/
document_errors(app)

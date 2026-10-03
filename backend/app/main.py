import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response

from app.api import health
from app.api.errors import register_error_handlers
from app.api.limits import BodyLimitMiddleware
from app.api.v1 import api_v1_router
from app.config import settings
from app.db.engine import make_engine
from app.db.guard import assert_safe_role


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    # Fail closed: a role that bypasses RLS must never serve traffic.
    engine = make_engine(settings.database_url)
    try:
        await assert_safe_role(engine)
    finally:
        await engine.dispose()
    yield


app = FastAPI(title="Agent Ops", lifespan=lifespan)
app.add_middleware(BodyLimitMiddleware)


@app.middleware("http")
async def request_id_middleware(request: Request, call_next) -> Response:
    req_id = request.headers.get("X-Request-ID") or str(uuid.uuid4())
    request.state.request_id = req_id
    response = await call_next(request)
    response.headers["X-Request-ID"] = req_id
    return response


register_error_handlers(app)
app.include_router(health.router)
app.include_router(api_v1_router)

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api import health
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
app.include_router(health.router)

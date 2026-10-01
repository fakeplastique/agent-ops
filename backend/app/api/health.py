import asyncio

from fastapi import APIRouter, Response
from redis.asyncio import Redis
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.config import settings

router = APIRouter()
TIMEOUT = 2  # seconds per dependency; readiness must answer fast


@router.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}


async def _postgres() -> bool:
    engine = create_async_engine(settings.database_url)
    try:
        async with asyncio.timeout(TIMEOUT), engine.connect() as conn:
            await conn.execute(text("select 1"))
        return True
    except Exception:  # noqa: BLE001 - any failure means not ready
        return False
    finally:
        await engine.dispose()


async def _redis() -> bool:
    client = Redis.from_url(settings.redis_url)
    try:
        async with asyncio.timeout(TIMEOUT):
            return bool(await client.ping())
    except Exception:  # noqa: BLE001 - any failure means not ready
        return False
    finally:
        await client.aclose()


@router.get("/readyz")
async def readyz(response: Response) -> dict[str, object]:
    postgres, redis = await asyncio.gather(_postgres(), _redis())
    ok = postgres and redis
    if not ok:
        response.status_code = 503
    return {
        "status": "ok" if ok else "unavailable",
        "checks": {"postgres": postgres, "redis": redis},
    }

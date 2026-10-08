import hmac
from typing import Annotated

from fastapi import APIRouter, Header
from fastapi.responses import PlainTextResponse

from app.config import settings
from app.domain.errors import NotFoundError
from app.services import cache

# Not in the public API (no OpenAPI entry): a scraper presents METRICS_TOKEN; unset = the route is off.
router = APIRouter(include_in_schema=False)


@router.get("/metrics", response_class=PlainTextResponse)
async def metrics(x_metrics_token: Annotated[str | None, Header()] = None) -> str:
    token = settings.metrics_token
    if not token or not hmac.compare_digest((x_metrics_token or "").encode(), token.encode()):
        raise NotFoundError  # indistinguishable from a missing route
    return cache.exposition()

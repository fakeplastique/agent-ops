import uuid
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel

from app.api import gating
from app.api.deps import ActorDep, EngineDep
from app.api.pagination import Page, paginate
from app.ports.search import MAX_WINDOW, SearchHit
from app.services import search as search_service

SEARCH_GATE = gating.gate("search")
router = APIRouter(tags=["search"])


class SearchHitOut(BaseModel):
    decision_request_id: uuid.UUID
    title: str
    status: str
    effective_risk_level: str
    created_at: datetime
    matched_in: list[str]
    rank: float

    @classmethod
    def of(cls, hit: SearchHit) -> "SearchHitOut":
        return cls(**vars(hit))


@router.get("/search", response_model=Page[SearchHitOut], dependencies=[Depends(SEARCH_GATE)])
async def search(
    request: Request,
    actor: ActorDep,
    engine: EngineDep,
    q: Annotated[
        str, Query(min_length=1, max_length=200, description="Words; quotes, OR and - work")
    ],
    skip: Annotated[int, Query(ge=0, le=MAX_WINDOW, description="Items to skip")] = 0,
    limit: Annotated[int, Query(ge=1, le=100, description="Max items to return (1-100)")] = 20,
) -> Page[SearchHitOut]:
    """Requests whose title, description or discussion match, best first (one hit per request). Reaches
    the best 1000 matches; votes are not searched."""
    page = await search_service.search(engine, actor.organization_id, q, skip, limit)
    return paginate(request, [SearchHitOut.of(h) for h in page.items], page.total, skip, limit)

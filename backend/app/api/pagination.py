from fastapi import Query, Request
from pydantic import BaseModel, Field


class PaginationParams:
    def __init__(
        self,
        skip: int = Query(0, ge=0, description="Items to skip"),
        limit: int = Query(20, ge=1, le=100, description="Max items to return (1-100)"),
    ):
        self.skip = skip
        self.limit = limit


class Page[T](BaseModel):
    items: list[T]
    total: int
    skip: int
    limit: int
    nextLink: str | None = Field(default=None, serialization_alias="nextLink")

    model_config = {"populate_by_name": True}


def paginate[T](
    request: Request,
    items: list[T],
    total: int,
    skip: int,
    limit: int,
) -> Page[T]:
    next_link: str | None = None
    if skip + limit < total:
        next_link = str(request.url.include_query_params(skip=skip + limit, limit=limit))
    return Page(
        items=items,
        total=total,
        skip=skip,
        limit=limit,
        nextLink=next_link,
    )

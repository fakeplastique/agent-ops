from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request

from app.api.pagination import Page, PaginationParams, paginate
from tests.api.harness import probe_client

pagination_router = APIRouter(prefix="/test-pagination")

DATA = [
    {"id": f"item-{i:03d}", "category": "A" if i % 2 == 0 else "B", "score": 10} for i in range(25)
]
ALLOWED_SORTS = {"id", "category", "score"}


@pagination_router.get("/items", response_model=Page[dict])
def list_items(
    request: Request,
    pagination: Annotated[PaginationParams, Depends()],
    sort: str = Query("id"),
):
    sort_field = sort.lstrip("-")
    if sort_field not in ALLOWED_SORTS:
        from app.domain.errors import InvalidInput

        raise InvalidInput(f"Unknown sort field: {sort_field}")

    reverse = sort.startswith("-")
    # sort with id as deterministic tiebreaker
    sorted_data = sorted(
        DATA,
        key=lambda x: (x[sort_field], x["id"]),
        reverse=reverse,
    )
    total = len(sorted_data)
    items = sorted_data[pagination.skip : pagination.skip + pagination.limit]
    return paginate(request, items, total, pagination.skip, pagination.limit)


client = probe_client(pagination_router)


def test_limit_0_101_negative_skip_unknown_sort_are_422():
    for params in [
        {"limit": 0},
        {"limit": 101},
        {"skip": -1},
        {"sort": "unknown_column"},
    ]:
        res = client.get("/test-pagination/items", params=params)
        assert res.status_code == 422, f"Params {params} got {res.status_code}"
        assert res.json()["error"]["code"] == "invalid_input"


def test_next_link_stable_with_ties():
    # Sort by score (which has ties for all 25 rows) with id tiebreak
    url = "/test-pagination/items?limit=10&sort=score"
    seen_ids = []
    while url:
        res = client.get(url)
        assert res.status_code == 200
        body = res.json()
        seen_ids.extend([item["id"] for item in body["items"]])
        url = body["nextLink"]

    assert len(seen_ids) == 25
    assert len(set(seen_ids)) == 25  # exactly 25 unique items, no duplicates or skips


def test_total_and_nextlink_null_on_last_page():
    res = client.get("/test-pagination/items?skip=20&limit=10")
    assert res.status_code == 200
    body = res.json()
    assert body["total"] == 25
    assert len(body["items"]) == 5
    assert body["skip"] == 20
    assert body["limit"] == 10
    assert body["nextLink"] is None


def test_skip_beyond_a_database_integer_is_422_not_500():
    # catches: an unbounded skip reaching asyncpg as an int64 overflow (500)
    res = client.get("/test-pagination/items", params={"skip": 10**20})
    assert res.status_code == 422
    assert client.get("/test-pagination/items", params={"skip": 1_000_000}).status_code == 200

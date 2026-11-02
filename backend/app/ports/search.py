import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

MAX_WINDOW = 1000  # how many best matches a query can page through; `skip` stops here


@dataclass(frozen=True)
class SearchHit:
    decision_request_id: uuid.UUID
    title: str
    status: str
    effective_risk_level: str
    created_at: datetime
    matched_in: list[str]  # any of "title", "description", "message"
    rank: float


@dataclass(frozen=True)
class SearchPage:
    items: list[SearchHit]
    total: int  # of the window, at most MAX_WINDOW


class Search(Protocol):
    async def query(self, organization_id: uuid.UUID, q: str, skip: int, limit: int) -> SearchPage:
        """Requests of this organization whose title, description or discussion match, best first.
        Only the organization's own data is searched; vote text is never part of the index."""
        ...

import uuid

from sqlalchemy.ext.asyncio import AsyncEngine

from app.adapters.search_pg import PgSearch
from app.ports.search import SearchPage
from app.services.validation import check_text

Q_MAX = 200


async def search(engine: AsyncEngine, o: uuid.UUID, q: str, skip: int, limit: int) -> SearchPage:
    """The one entry for REST and MCP. NUL, a lone surrogate or a blank query is a 422 here, not a driver error."""
    check_text("q", q, 1, Q_MAX)
    return await PgSearch(engine).query(o, q, skip, limit)

from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine


def make_engine(url: str) -> AsyncEngine:
    """Every engine the app builds. hide_parameters keeps bound values (payloads, ids, hashes) out of the
    error messages SQLAlchemy raises and logs: logs and error bodies never contain payloads (D21)."""
    return create_async_engine(url, hide_parameters=True)

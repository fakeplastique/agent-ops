import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, AsyncSession


async def use_tenant(conn: AsyncConnection, org_id: uuid.UUID) -> None:
    """Point an open transaction at a tenant that is only known (or created) inside it."""
    await conn.execute(
        text("select set_config('app.organization_id', :o, true)"), {"o": str(org_id)}
    )


LOOKUP_KEYS = frozenset({"app.self_user_id", "app.invite_hash", "app.receipt_request_id"})


async def lookup_as(conn: AsyncConnection, key: str, value: str) -> None:
    """Name the one row a pre-tenant lookup policy may show (transaction-local), e.g. `app.invite_hash`."""
    if key not in LOOKUP_KEYS:  # never a way round use_tenant, the one writer of the tenant setting
        raise ValueError(key)
    await conn.execute(text("select set_config(:k, :v, true)"), {"k": key, "v": value})


@asynccontextmanager
async def tenant_session(engine: AsyncEngine, org_id: uuid.UUID) -> AsyncIterator[AsyncSession]:
    """One transaction scoped to a tenant; commits on exit, rolls back on error.

    Hooks in `session.info["after_commit"]` (sync, returning an awaitable or None) run after the commit."""
    async with AsyncSession(engine, expire_on_commit=False) as session:
        async with session.begin():
            # is_local=true: the setting dies with the transaction, so pooled connections never leak it.
            await session.execute(
                text("select set_config('app.organization_id', :o, true)"), {"o": str(org_id)}
            )
            session.info["organization_id"] = org_id  # what the cache may serve this session
            yield session
        # Every hook starts before the first await, so a cancellation cannot skip one.
        started = [hook() for hook in session.info.pop("after_commit", ())]
        for pending in started:
            if pending is not None:
                await pending

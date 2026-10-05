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


async def lookup_as(conn: AsyncConnection, key: str, value: str) -> None:
    """Name the one row a pre-tenant lookup policy may show (transaction-local), e.g. `app.invite_hash`."""
    await conn.execute(text("select set_config(:k, :v, true)"), {"k": key, "v": value})


@asynccontextmanager
async def tenant_session(engine: AsyncEngine, org_id: uuid.UUID) -> AsyncIterator[AsyncSession]:
    """One transaction scoped to a tenant; commits on exit, rolls back on error."""
    async with AsyncSession(engine, expire_on_commit=False) as session, session.begin():
        # is_local=true: the setting dies with the transaction, so pooled connections never leak it.
        await session.execute(
            text("select set_config('app.organization_id', :o, true)"), {"o": str(org_id)}
        )
        yield session

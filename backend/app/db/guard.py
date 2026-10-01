from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

# Superuser/BYPASSRLS skip RLS; owning a table skips it too unless FORCE is set.
_ROLE_SQL = text(
    """
    select r.rolsuper or r.rolbypassrls as bypass,
           exists (select 1 from pg_class c
                   where c.relnamespace = 'public'::regnamespace and c.relowner = r.oid) as owner
    from pg_roles r where r.rolname = current_user
    """
)


class UnsafeDatabaseRoleError(RuntimeError):
    code = "unsafe_database_role"


async def assert_safe_role(engine: AsyncEngine) -> None:
    """Refuse to run as a role that could silently bypass tenant isolation."""
    async with engine.connect() as c:
        bypass, owner = (await c.execute(_ROLE_SQL)).one()
    if bypass or owner:
        raise UnsafeDatabaseRoleError(
            "DATABASE_URL role is a superuser, has BYPASSRLS or owns tables; use the app role"
        )

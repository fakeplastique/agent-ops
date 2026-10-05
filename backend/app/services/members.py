import re
import uuid

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.db.session import use_tenant
from app.domain.errors import InvalidInput, LastOrgAdmin, NotFoundError, SlugTaken
from app.services import audit
from app.services.invitations import ROLES
from app.services.validation import check_text

_SLUG = re.compile(r"[a-z0-9][a-z0-9-]{1,38}[a-z0-9]")
_LIST = text(
    "select m.user_id, u.email, u.full_name, m.role, m.team_id, count(*) over () as total from memberships m"
    " join users u on u.id = m.user_id where m.organization_id = :o order by u.email, m.user_id"
    " offset :skip limit :limit"
)
_ROLE = text(
    "update memberships set role = :r, updated_at = now() where organization_id = :o and user_id = :u returning 1"
)
_REMOVE = text("delete from memberships where organization_id = :o and user_id = :u returning 1")


def _last_admin(e: IntegrityError) -> Exception:
    """The database trigger is the one place that counts admins; any other integrity error is a real bug."""
    return LastOrgAdmin() if "keeps at least one org_admin" in str(e) else e


async def create_organization(
    engine: AsyncEngine, user_id: uuid.UUID, name: str, slug: str
) -> uuid.UUID:
    """A new organization with its creator as the first org_admin, in one transaction (never an org without one)."""
    check_text("name", name, 1, 80)
    if not _SLUG.fullmatch(slug):
        raise InvalidInput("slug must be 3-40 characters: lowercase letters, digits and hyphens")
    org = uuid.uuid4()
    try:
        async with engine.begin() as c:
            await use_tenant(c, org)  # the tenant root is its own tenant
            await c.execute(
                text("insert into organizations (id, name, slug) values (:o, :n, :s)"),
                {"o": org, "n": name.strip(), "s": slug},
            )
            await c.execute(
                text(
                    "insert into memberships (organization_id, user_id, role) values (:o, :u, 'org_admin')"
                ),
                {"o": org, "u": user_id},
            )
            await c.execute(
                text(
                    "insert into audit_logs (organization_id, entity_type, entity_id, action, actor_type, actor_id)"
                    " values (:o, 'organization', :o, 'organization_created', 'user', :u)"
                ),
                {"o": org, "u": user_id},
            )
    except IntegrityError as e:
        raise SlugTaken from e
    return org


async def list_members(
    session: AsyncSession, org: uuid.UUID, skip: int, limit: int
) -> tuple[list[dict], int]:
    rows = (await session.execute(_LIST, {"o": org, "skip": skip, "limit": limit})).all()
    return [dict(r._mapping) for r in rows], (rows[0].total if rows else 0)


async def set_role(
    session: AsyncSession,
    org: uuid.UUID,
    user_id: uuid.UUID,
    role: str,
    actor: uuid.UUID | None = None,
) -> None:
    if role not in ROLES:
        raise InvalidInput(f"role must be one of {', '.join(ROLES)}")
    try:
        async with session.begin_nested():
            done = (await session.execute(_ROLE, {"o": org, "u": user_id, "r": role})).first()
    except IntegrityError as e:
        raise _last_admin(e) from e
    if done is None:
        raise NotFoundError
    await audit.record(
        session, org, "membership", user_id, "member_role_changed", "user", actor, {"role": role}
    )


async def remove_member(
    session: AsyncSession, org: uuid.UUID, user_id: uuid.UUID, actor: uuid.UUID | None = None
) -> None:
    """Leaving the organization also kills the user's personal tokens there (their FK cascades)."""
    try:
        async with session.begin_nested():
            done = (await session.execute(_REMOVE, {"o": org, "u": user_id})).first()
    except IntegrityError as e:
        raise _last_admin(e) from e
    if done is None:
        raise NotFoundError
    await audit.record(session, org, "membership", user_id, "member_removed", "user", actor)

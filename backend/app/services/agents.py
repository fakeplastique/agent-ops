import uuid

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.errors import NameTaken, NotFoundError
from app.services import audit
from app.services.tokens import issue_agent_token
from app.services.validation import check_text

_COLS = "id, name, description, vendor, owner_user_id, status, created_at"
_MEMBER = text("select 1 from memberships where organization_id = :o and user_id = :u")
_INSERT = text(
    "insert into agent_identities (organization_id, name, description, vendor, owner_user_id)"
    f" values (:o, :n, :d, :v, :u) returning {_COLS}"
)
_LIST = text(
    f"select {_COLS}, count(*) over () as total from agent_identities where organization_id = :o"
    " order by name, id offset :skip limit :limit"
)
_ONE = text(f"select {_COLS} from agent_identities where organization_id = :o and id = :i")


async def create_agent(
    session: AsyncSession, org: uuid.UUID, actor: uuid.UUID, name: str, description: str,
    vendor: str | None, owner_user_id: uuid.UUID,
) -> tuple[dict, str]:  # fmt: skip
    """A new identity and its first token (shown once). The owner must be a member of this org."""
    check_text("name", name, 1, 80)
    check_text("description", description, 0, 1000)
    if vendor is not None:
        check_text("vendor", vendor, 1, 80)
    if (await session.execute(_MEMBER, {"o": org, "u": owner_user_id})).first() is None:
        raise NotFoundError
    params = {"o": org, "n": name, "d": description, "v": vendor, "u": owner_user_id}
    try:
        async with session.begin_nested():
            row = (await session.execute(_INSERT, params)).mappings().one()
    except IntegrityError as e:  # (organization_id, name) is the only unique a valid input can hit
        raise NameTaken("an agent with this name exists") from e
    await audit.record(session, org, "agent_identity", row["id"], "created", "user", actor)
    return dict(row), await issue_agent_token(session, org, row["id"], actor)


async def list_agents(
    session: AsyncSession, org: uuid.UUID, skip: int, limit: int
) -> tuple[list[dict], int]:
    rows = (await session.execute(_LIST, {"o": org, "skip": skip, "limit": limit})).mappings().all()
    return [dict(r) for r in rows], (rows[0]["total"] if rows else 0)


async def get_agent(session: AsyncSession, org: uuid.UUID, agent_id: uuid.UUID) -> dict:
    row = (await session.execute(_ONE, {"o": org, "i": agent_id})).mappings().first()
    if row is None:
        raise NotFoundError
    return dict(row)

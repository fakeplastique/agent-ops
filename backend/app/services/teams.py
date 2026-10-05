import uuid
from typing import Any

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.errors import InvalidInput, NameTaken, NotFoundError, TeamInUse
from app.services import audit
from app.services.validation import check_text

_COLS = "id, name, parent_team_id, created_at"
_LIST = text(
    f"select {_COLS}, count(*) over () as total from teams where organization_id = :o"
    " order by name, id offset :skip limit :limit"
)
_ONE = text(f"select {_COLS} from teams where organization_id = :o and id = :t")
_INSERT = text(
    f"insert into teams (organization_id, name, parent_team_id) values (:o, :n, :p) returning {_COLS}"
)
_UPDATE = text(
    "update teams set name = :n, parent_team_id = :p, updated_at = now()"
    f" where organization_id = :o and id = :t returning {_COLS}"
)
# One tree writer per organization: two moves that are each fine alone cannot both pass the check below.
# Transaction-scoped; READ COMMITTED lets the waiter's next statement see the move it waited for.
_TREE_LOCK = text("select pg_advisory_xact_lock(hashtextextended(:k, 0))")
_DELETE = text("delete from teams where organization_id = :o and id = :t returning id")
# The new parent's ancestors (itself included); UNION stops even on a chain that already loops.
_ANCESTOR = text(
    "with recursive up as ("
    " select id, parent_team_id from teams where organization_id = :o and id = :p"
    " union select t.id, t.parent_team_id from teams t join up on t.id = up.parent_team_id"
    " where t.organization_id = :o"
    ") select 1 from up where id = :t"
)


def _unique(e: IntegrityError) -> Exception:
    return NameTaken("a team with this name exists") if _sqlstate(e) == "23505" else e


def _sqlstate(e: IntegrityError) -> str | None:
    return getattr(e.orig, "sqlstate", None)


async def _parent_exists(s: AsyncSession, org: uuid.UUID, parent: uuid.UUID | None) -> None:
    # Explicit org predicate: another tenant's team is "not found", never a 409 or a FK 500.
    if parent is not None and (await s.execute(_ONE, {"o": org, "t": parent})).first() is None:
        raise NotFoundError("parent team not found")


async def create_team(
    s: AsyncSession, org: uuid.UUID, actor: uuid.UUID, name: str, parent: uuid.UUID | None
) -> dict:
    check_text("name", name, 1, 80)
    await _parent_exists(s, org, parent)
    try:
        async with s.begin_nested():
            row = (await s.execute(_INSERT, {"o": org, "n": name, "p": parent})).mappings().one()
    except IntegrityError as e:
        raise _unique(e) from e
    await audit.record(s, org, "team", row["id"], "created", "user", actor)
    return dict(row)


async def list_teams(
    s: AsyncSession, org: uuid.UUID, skip: int, limit: int
) -> tuple[list[dict], int]:
    rows = (await s.execute(_LIST, {"o": org, "skip": skip, "limit": limit})).mappings().all()
    return [dict(r) for r in rows], (rows[0]["total"] if rows else 0)


async def get_team(s: AsyncSession, org: uuid.UUID, team: uuid.UUID) -> dict:
    row = (await s.execute(_ONE, {"o": org, "t": team})).mappings().first()
    if row is None:
        raise NotFoundError
    return dict(row)


async def update_team(
    s: AsyncSession, org: uuid.UUID, actor: uuid.UUID, team: uuid.UUID, fields: dict[str, Any]
) -> dict:
    """Rename and/or move; a move that would make the team its own ancestor is rejected."""
    if "parent_team_id" in fields:
        await s.execute(_TREE_LOCK, {"k": f"teams:{org}"})
    current = await get_team(s, org, team)
    name = fields.get("name", current["name"])
    parent = fields.get("parent_team_id", current["parent_team_id"])
    check_text("name", name, 1, 80)
    if parent is not None and parent != current["parent_team_id"]:
        await _parent_exists(s, org, parent)
        if (await s.execute(_ANCESTOR, {"o": org, "p": parent, "t": team})).first():
            raise InvalidInput("a team cannot sit under itself or one of its sub-teams")
    try:
        async with s.begin_nested():
            row = (
                (await s.execute(_UPDATE, {"o": org, "t": team, "n": name, "p": parent}))
                .mappings()
                .one()
            )
    except IntegrityError as e:
        raise _unique(e) from e
    await audit.record(s, org, "team", team, "updated", "user", actor)
    return dict(row)


async def delete_team(s: AsyncSession, org: uuid.UUID, actor: uuid.UUID, team: uuid.UUID) -> None:
    try:
        async with s.begin_nested():
            done = (await s.execute(_DELETE, {"o": org, "t": team})).first()
    except IntegrityError as e:  # sub-teams, members, invitations or a policy still point at it
        raise TeamInUse from e
    if done is None:
        raise NotFoundError
    await audit.record(s, org, "team", team, "deleted", "user", actor)

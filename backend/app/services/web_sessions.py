import hashlib
import secrets
import uuid
from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.db.session import tenant_session
from app.domain.errors import NotFoundError
from app.ports.clock import Clock

IDLE = timedelta(hours=12)  # sliding
ABSOLUTE = timedelta(days=30)  # the cap no activity extends
_SET_HASH = text("select set_config('app.session_hash', :h, true)")
_SET_EMAIL = text("select set_config('app.login_email', :e, true)")
_FIND_USER = text("select id from users where email = :e")
_NEW_USER = text(
    "insert into users (email, full_name) values (:e, :n) on conflict (email) do nothing returning id"
)
_INSERT = text(
    "insert into web_sessions (id_hash, user_id, created_at, last_seen_at, expires_at)"
    " values (:h, :u, :now, :now, :exp)"
)
_LOAD = text("select user_id, selected_organization_id, last_seen_at, expires_at from web_sessions")
_TOUCH = text("update web_sessions set last_seen_at = :now")
_DELETE = text("delete from web_sessions")
_SELECT_ORG = text("update web_sessions set selected_organization_id = :o")
_ROLE = text("select role from memberships where organization_id = :o and user_id = :u")


@dataclass(frozen=True)
class WebSession:
    user_id: uuid.UUID
    selected_organization_id: uuid.UUID | None


def _hash(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


async def find_or_create_user(engine: AsyncEngine, email: str, full_name: str) -> uuid.UUID:
    """The user for a verified, lower-cased email; the first login creates it. Never an admin from a login."""
    async with engine.begin() as conn:
        await conn.execute(
            _SET_EMAIL, {"e": email}
        )  # the policies show and allow exactly this one email
        found = (await conn.execute(_FIND_USER, {"e": email})).scalar()
        if found is None:
            found = (await conn.execute(_NEW_USER, {"e": email, "n": full_name})).scalar()
        if found is None:  # lost a race with a concurrent first login
            found = (await conn.execute(_FIND_USER, {"e": email})).scalar_one()
        return found


async def find_user(engine: AsyncEngine, email: str) -> uuid.UUID | None:
    """The existing user for a lower-cased email, never creating one."""
    async with engine.begin() as conn:
        await conn.execute(_SET_EMAIL, {"e": email})
        return (await conn.execute(_FIND_USER, {"e": email})).scalar()


async def end_session(engine: AsyncEngine, raw: str | None) -> None:
    if not raw:
        return
    async with engine.begin() as conn:
        await conn.execute(_SET_HASH, {"h": _hash(raw)})
        await conn.execute(_DELETE)


async def start_session(
    engine: AsyncEngine, user_id: uuid.UUID, clock: Clock, previous: str | None = None
) -> str:
    """A fresh session id on every login, never one the client brought: the previous session (if any) is
    deleted, so a pre-set or stolen cookie cannot survive a login (session fixation)."""
    await end_session(engine, previous)
    raw = secrets.token_urlsafe(32)
    now = clock.now()
    async with engine.begin() as conn:
        await conn.execute(_SET_HASH, {"h": _hash(raw)})
        await conn.execute(
            _INSERT, {"h": _hash(raw), "u": user_id, "now": now, "exp": now + ABSOLUTE}
        )
    return raw


async def load_session(engine: AsyncEngine, raw: str | None, clock: Clock) -> WebSession | None:
    """The live session for a cookie value, sliding its idle window; expired rows are deleted."""
    if not raw:
        return None
    now = clock.now()
    async with engine.begin() as conn:
        await conn.execute(_SET_HASH, {"h": _hash(raw)})
        row = (await conn.execute(_LOAD)).first()
        if row is None:
            return None
        if now >= row.expires_at or now >= row.last_seen_at + IDLE:
            await conn.execute(_DELETE)
            return None
        await conn.execute(_TOUCH, {"now": now})
    return WebSession(row.user_id, row.selected_organization_id)


async def role_in(
    engine: AsyncEngine, user_id: uuid.UUID, organization_id: uuid.UUID
) -> str | None:
    """The user's current role there, read now (never cached in the session): None once they left."""
    async with tenant_session(engine, organization_id) as session:
        return (await session.execute(_ROLE, {"o": organization_id, "u": user_id})).scalar()


async def select_organization(
    engine: AsyncEngine, raw: str, user_id: uuid.UUID, organization_id: uuid.UUID
) -> str:
    """Switch the session's organization; a non-member gets the same NotFound as a missing organization."""
    role = await role_in(engine, user_id, organization_id)
    if role is None:
        raise NotFoundError
    async with engine.begin() as conn:
        await conn.execute(_SET_HASH, {"h": _hash(raw)})
        await conn.execute(_SELECT_ORG, {"o": organization_id})
    return role

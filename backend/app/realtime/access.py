import uuid
from dataclasses import dataclass
from typing import Literal

from sqlalchemy.ext.asyncio import AsyncEngine

from app.authz.matrix import Role
from app.authz.resolver import bearer_digest, pat_live
from app.domain.errors import NotFoundError
from app.ports.clock import Clock
from app.services.web_sessions import digest, role_in, session_live


@dataclass(frozen=True)
class Credential:
    """Which credential a feed rides on, by its stored hash (never the token or cookie value)."""

    kind: Literal["pat", "session"]
    digest: str


def credential_of(authorization: str | None, cookie: str | None) -> Credential:
    """The credential the caller was resolved with: the bearer token if sent, else the session cookie."""
    if authorization is not None:
        return Credential("pat", bearer_digest(authorization))
    return Credential("session", digest(cookie or ""))


async def recheck(
    engine: AsyncEngine, cred: Credential, user: uuid.UUID, org: uuid.UUID, clock: Clock
) -> Role:
    """The role to project with now, or NotFoundError once the credential, the session's organization or
    the membership lapsed. Long-poll and WebSocket share it; a lapse is never counted as a failed login."""
    if cred.kind == "pat":
        live = await pat_live(engine, org, user, cred.digest)
    else:
        live = await session_live(engine, cred.digest, user, org, clock)
    role = await role_in(engine, user, org) if live else None
    if role is None:
        raise NotFoundError
    return Role[role.upper()]

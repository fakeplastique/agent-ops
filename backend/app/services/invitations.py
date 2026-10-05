import hashlib
import re
import uuid
from datetime import timedelta

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.db.session import lookup_as, use_tenant
from app.domain.errors import (
    AlreadyMember,
    InvalidInput,
    InvitationEmailMismatch,
    InvitationUnusable,
    NotFoundError,
)
from app.ports.clock import Clock
from app.services import audit
from app.services.tokens import new_token
from app.services.validation import check_text

TTL = timedelta(days=7)
TAG = "inv_"
ROLES = ("member", "team_lead", "manager", "org_admin")
_TOKEN = re.compile(r"inv_[A-Za-z0-9_-]{43}")
_EMAIL = re.compile(r"[^@\s]{1,64}@[^@\s]{1,189}")

_CREATE = text(
    "insert into invitations (organization_id, email, role, team_id, token_hash, invited_by, created_at, expires_at)"
    " values (:o, :e, :r, :t, :h, :by, :now, :exp) returning id"
)
_REVOKE = text(
    "update invitations set revoked_at = :now where organization_id = :o and id = :i"
    " and accepted_at is null and revoked_at is null returning id"
)
_LIST = text(
    "select id, email, role, team_id, created_at, expires_at from invitations where organization_id = :o"
    " and accepted_at is null and revoked_at is null and expires_at > :now order by created_at desc, id"
)
# One atomic UPDATE is the whole race: a second accept waits on the row lock, re-checks the WHERE and gets nothing.
_ACCEPT = text(
    "update invitations set accepted_at = :now, accepted_by = :u where token_hash = :h and accepted_at is null"
    " and revoked_at is null and expires_at > :now and email = :e returning id, organization_id, role, team_id"
)
_PEEK = text("select email, accepted_at, revoked_at, expires_at from invitations")
_JOIN = text(
    "insert into memberships (organization_id, user_id, team_id, role) values (:o, :u, :t, :r)"
    " on conflict (organization_id, user_id) do nothing returning id"
)
_AUDIT = text(
    "insert into audit_logs (organization_id, entity_type, entity_id, action, actor_type, actor_id, data)"
    " values (:o, 'invitation', :i, 'invitation_accepted', 'user', :u, cast(:d as jsonb))"
)


async def create_invitation(
    session: AsyncSession,
    org: uuid.UUID,
    inviter: uuid.UUID,
    email: str,
    role: str,
    team_id: uuid.UUID | None,
    clock: Clock,
) -> tuple[uuid.UUID, str]:
    """A 7-day invitation; the raw token is returned once and only its hash is stored."""
    check_text("email", email, 3, 254)
    email = email.strip().lower()
    if not _EMAIL.fullmatch(email):
        raise InvalidInput("email is not valid")
    if role not in ROLES:
        raise InvalidInput(f"role must be one of {', '.join(ROLES)}")
    raw, _, digest = new_token(TAG)
    now = clock.now()
    args = {"o": org, "e": email, "r": role, "t": team_id, "h": digest, "by": inviter}
    try:
        async with session.begin_nested():
            inv = (
                await session.execute(_CREATE, {**args, "now": now, "exp": now + TTL})
            ).scalar_one()
    except IntegrityError as e:  # the only foreign key a caller controls is the team
        raise InvalidInput("unknown team") from e
    await audit.record(
        session, org, "invitation", inv, "invitation_created", "user", inviter, {"role": role}
    )
    return inv, raw


async def revoke_invitation(
    session: AsyncSession, org: uuid.UUID, invitation_id: uuid.UUID, clock: Clock
) -> None:
    done = await session.execute(_REVOKE, {"o": org, "i": invitation_id, "now": clock.now()})
    if done.first() is None:
        raise NotFoundError
    await audit.record(session, org, "invitation", invitation_id, "invitation_revoked", "user")


async def pending_invitations(session: AsyncSession, org: uuid.UUID, clock: Clock) -> list[dict]:
    rows = await session.execute(_LIST, {"o": org, "now": clock.now()})
    return [dict(r._mapping) for r in rows]


async def accept_invitation(
    engine: AsyncEngine, user_id: uuid.UUID, raw: str, clock: Clock
) -> tuple[uuid.UUID, str]:
    """Join the invited organization with the invited role, once, as the user whose email was invited."""
    if not _TOKEN.fullmatch(raw):
        raise InvitationUnusable
    digest = hashlib.sha256(raw.encode()).hexdigest()
    now = clock.now()
    async with engine.begin() as c:
        await lookup_as(c, "app.self_user_id", str(user_id))
        await lookup_as(c, "app.invite_hash", digest)
        email = (await c.execute(text("select email from users"))).scalar()
        if email is None:
            raise InvitationUnusable
        got = (
            await c.execute(_ACCEPT, {"h": digest, "now": now, "u": user_id, "e": email})
        ).first()
        if got is None:
            seen = (await c.execute(_PEEK)).first()
            if (
                seen is not None
                and seen.accepted_at is None
                and seen.revoked_at is None
                and seen.expires_at > now
            ):
                if (
                    seen.email != email
                ):  # a usable link on the wrong account: say which one it is for
                    raise InvitationEmailMismatch(seen.email)
            raise InvitationUnusable
        inv, org, role, team = got
        await use_tenant(c, org)
        joined = (await c.execute(_JOIN, {"o": org, "u": user_id, "t": team, "r": role})).first()
        if joined is None:  # already a member: roll the acceptance back so the link is not burned
            raise AlreadyMember
        await c.execute(_AUDIT, {"o": org, "i": inv, "u": user_id, "d": f'{{"role": "{role}"}}'})
        return org, role

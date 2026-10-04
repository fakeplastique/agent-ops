import hashlib
import hmac
import re
import uuid
from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.authz.matrix import Role
from app.authz.principals import AgentPrincipal, Principal, UserPrincipal
from app.db.session import tenant_session
from app.domain.errors import Unauthenticated

# The only place a request's credential is read. Nothing else in app/ looks at the header (a test greps for it).
_BEARER = re.compile(r"Bearer (aop|pat)_([A-Za-z0-9_-]{43})")
_PREFIX_LEN = 8
_TABLES = {
    "aop": ("agent_tokens", "agent_identity_id"),
    "pat": ("personal_access_tokens", "user_id"),
}


@dataclass(frozen=True)
class Resolved:
    principal: Principal
    organization_id: uuid.UUID


async def resolve_bearer(engine: AsyncEngine, authorization: str | None) -> Resolved:
    """`Authorization: Bearer aop_...|pat_...` -> (principal, organization), read from the database on every
    request (no cache: a revoked or rotated token fails on the next call). The organization is the token's own,
    never taken from a header or a body. Every failure is the same Unauthenticated."""
    match = _BEARER.fullmatch(authorization or "")
    if match is None:
        raise Unauthenticated
    tag, body = match.groups()
    raw = f"{tag}_{body}"
    digest = hashlib.sha256(raw.encode()).hexdigest()
    table, subject_col = _TABLES[tag]
    prefix = body[:_PREFIX_LEN]
    async with engine.begin() as conn:
        # The tenant is not known yet: the lookup policy shows only rows whose prefix this transaction names.
        await conn.execute(text("select set_config('app.lookup_prefix', :p, true)"), {"p": prefix})
        rows = (
            (
                await conn.execute(
                    text(
                        f"select organization_id, {subject_col} as subject, token_hash, revoked_at from {table} where token_prefix = :p"
                    ),
                    {"p": prefix},
                )
            )
            .mappings()
            .all()
        )
    # Constant-time compare against every candidate (a prefix can collide): no early exit.
    matches = [r for r in rows if hmac.compare_digest(r["token_hash"].encode(), digest.encode())]
    if (
        not matches or matches[0]["revoked_at"] is not None
    ):  # token_hash is unique: at most one match
        raise Unauthenticated
    org, subject = matches[0]["organization_id"], matches[0]["subject"]
    async with tenant_session(engine, org) as session:
        # The credential is checked again inside the tenant transaction: a revoke, a rotation or a removed
        # member between the two transactions must not let this request through on a dead credential.
        live = (
            await session.execute(
                text(
                    f"select 1 from {table} where organization_id = :o and token_hash = :h and revoked_at is null"
                ),
                {"o": org, "h": digest},
            )
        ).first()
        if live is None:
            raise Unauthenticated
        if tag == "aop":
            status = (
                await session.execute(
                    text(
                        "select status from agent_identities where organization_id = :o and id = :i"
                    ),
                    {"o": org, "i": subject},
                )
            ).scalar()
            if status != "active":
                raise Unauthenticated
            return Resolved(AgentPrincipal(subject, org), org)
        role = (
            await session.execute(
                text("select role from memberships where organization_id = :o and user_id = :u"),
                {"o": org, "u": subject},
            )
        ).scalar()
        if (
            role is None
        ):  # unreachable (the membership's cascade deleted the PAT above); a 401, never a 500
            raise Unauthenticated
        return Resolved(UserPrincipal(subject, org, Role[role.upper()]), org)

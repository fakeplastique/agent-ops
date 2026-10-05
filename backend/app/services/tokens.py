import hashlib
import secrets
import uuid

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.errors import NotFoundError
from app.services import audit
from app.services.validation import check_text

AGENT_TAG, PAT_TAG = "aop_", "pat_"
PREFIX_LEN = 8
_IDENTITY = text("select status from agent_identities where organization_id = :o and id = :i")
_MEMBER = text("select 1 from memberships where organization_id = :o and user_id = :u")
_ISSUE = text(
    "insert into agent_tokens (organization_id, agent_identity_id, token_prefix, token_hash)"
    " values (:o, :i, :p, :h) on conflict (agent_identity_id) do update"
    " set token_prefix = excluded.token_prefix, token_hash = excluded.token_hash, revoked_at = null"
)
_REVOKE_IDENTITY = text(
    "update agent_identities set status = 'revoked', updated_at = now() where organization_id = :o and id = :i"
)
_REVOKE_AGENT_TOKEN = text(
    "update agent_tokens set revoked_at = now() where organization_id = :o and agent_identity_id = :i"
)
_CREATE_PAT = text(
    "insert into personal_access_tokens (organization_id, user_id, name, token_prefix, token_hash)"
    " values (:o, :u, :n, :p, :h) returning id"
)
# A user-scoped revoke names its owner: another user's token id is "not found", even for an org_admin.
_REVOKE_PAT = text(
    "update personal_access_tokens set revoked_at = now() where organization_id = :o and id = :t"
    " and user_id = coalesce(cast(:u as uuid), user_id) and revoked_at is null returning id"
)
_PAT_COLS = "id, name, created_at, revoked_at"  # never token_hash or token_prefix
_LIST_PATS = text(
    f"select {_PAT_COLS}, count(*) over () as total from personal_access_tokens"
    " where organization_id = :o and user_id = :u order by created_at desc, id offset :skip limit :limit"
)
_ONE_PAT = text(
    f"select {_PAT_COLS} from personal_access_tokens where organization_id = :o and user_id = :u and id = :t"
)


def new_token(tag: str) -> tuple[str, str, str]:
    """(raw token, lookup prefix, SHA-256 hex). The raw token exists only here and in the caller's one reply."""
    body = secrets.token_urlsafe(32)  # 32 random bytes -> 43 url-safe characters
    raw = tag + body
    return raw, body[:PREFIX_LEN], hashlib.sha256(raw.encode()).hexdigest()


async def issue_agent_token(
    session: AsyncSession, organization_id: uuid.UUID, agent_identity_id: uuid.UUID,
    actor_id: uuid.UUID | None = None,
) -> str:  # fmt: skip
    """Mint an agent's token, or rotate it: the old token stops working in the same transaction. Shown once."""
    o = organization_id
    status = (await session.execute(_IDENTITY, {"o": o, "i": agent_identity_id})).scalar()
    if status != "active":  # unknown, another tenant's, or revoked: indistinguishable
        raise NotFoundError
    raw, prefix, digest = new_token(AGENT_TAG)
    await session.execute(_ISSUE, {"o": o, "i": agent_identity_id, "p": prefix, "h": digest})
    await audit.record(  # the audit row names the identity, never the token or its hash
        session, o, "agent_identity", agent_identity_id, "token_issued",
        "user" if actor_id else "system", actor_id,
    )  # fmt: skip
    return raw


async def revoke_agent(
    session: AsyncSession, organization_id: uuid.UUID, agent_identity_id: uuid.UUID,
    actor_id: uuid.UUID | None = None,
) -> None:  # fmt: skip
    """Revoke the identity and its token: effective on the very next request (the resolver reads the database)."""
    o = organization_id
    status = (await session.execute(_IDENTITY, {"o": o, "i": agent_identity_id})).scalar()
    if status is None:
        raise NotFoundError
    if status == "revoked":  # idempotent: no second timestamp, no second audit row
        return
    await session.execute(_REVOKE_IDENTITY, {"o": o, "i": agent_identity_id})
    await session.execute(_REVOKE_AGENT_TOKEN, {"o": o, "i": agent_identity_id})
    await audit.record(
        session, o, "agent_identity", agent_identity_id, "revoked",
        "user" if actor_id else "system", actor_id,
    )  # fmt: skip


async def create_pat(
    session: AsyncSession, organization_id: uuid.UUID, user_id: uuid.UUID, name: str
) -> tuple[uuid.UUID, str]:
    """A personal access token bound to this organization and user. Shown once."""
    o = organization_id
    check_text("name", name, 1, 80)
    if (await session.execute(_MEMBER, {"o": o, "u": user_id})).first() is None:
        raise NotFoundError
    raw, prefix, digest = new_token(PAT_TAG)
    pat_id = (
        await session.execute(
            _CREATE_PAT, {"o": o, "u": user_id, "n": name, "p": prefix, "h": digest}
        )
    ).scalar_one()
    await audit.record(session, o, "personal_access_token", pat_id, "created", "user", user_id)
    return pat_id, raw


async def revoke_pat(
    session: AsyncSession, organization_id: uuid.UUID, pat_id: uuid.UUID,
    user_id: uuid.UUID | None = None,
) -> None:  # fmt: skip
    o = organization_id
    row = (await session.execute(_REVOKE_PAT, {"o": o, "t": pat_id, "u": user_id})).first()
    if row is None:
        raise NotFoundError
    await audit.record(
        session, o, "personal_access_token", pat_id, "revoked",
        "user" if user_id else "system", user_id,
    )  # fmt: skip


async def list_pats(
    session: AsyncSession, org: uuid.UUID, user_id: uuid.UUID, skip: int, limit: int
) -> tuple[list[dict], int]:
    params = {"o": org, "u": user_id, "skip": skip, "limit": limit}
    rows = (await session.execute(_LIST_PATS, params)).mappings().all()
    return [dict(r) for r in rows], (rows[0]["total"] if rows else 0)


async def get_pat(
    session: AsyncSession, org: uuid.UUID, user_id: uuid.UUID, pat_id: uuid.UUID
) -> dict:
    row = (await session.execute(_ONE_PAT, {"o": org, "u": user_id, "t": pat_id})).mappings().first()
    if row is None:
        raise NotFoundError
    return dict(row)

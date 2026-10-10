"""Telegram linking and button presses (D19). Every press is authorized from the Telegram account that
pressed it, through the same decide_step as REST; nothing in the button names a step."""

import hashlib
import re
import secrets
import uuid
from datetime import datetime, timedelta

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.db.session import lookup_as, use_tenant
from app.ports.clock import Clock
from app.services import features

FLAG = "telegram"
CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # no 0/O or 1/I: the code may be typed
CODE_LEN = 8
CODE_TTL = timedelta(minutes=10)
_CODE = re.compile(f"[{CODE_ALPHABET}]{{{CODE_LEN}}}")

PRIVATE_ONLY = "Link your account in a private chat with the bot."
BAD_CODE = "This code is invalid or expired. Get a new one in Agent Ops settings."
NEEDS_PRO = "Telegram approvals need the Pro plan."
TAKEN = "This Telegram account is already linked to another Agent Ops user."
LINKED = "Linked. Approvals you may decide will arrive here."

_ISSUE = text(
    "insert into telegram_link_codes (organization_id, user_id, code_hash, expires_at)"
    " values (:o, :u, :h, :exp)"
)
# Single use under concurrency: the conditional update is the claim, a second redeemer matches nothing.
_USE_CODE = text(
    "update telegram_link_codes set used_at = :now where code_hash = :h and used_at is null"
    " and expires_at > :now returning organization_id, user_id"
)
_SET_CHAT = text("update users set telegram_chat_id = :c where id = :u")
_CHAT = text("select telegram_chat_id from users where id = :u")


def digest(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


async def issue_code(
    session: AsyncSession, org: uuid.UUID, user_id: uuid.UUID, clock: Clock
) -> tuple[str, datetime]:
    """A fresh one-time code (shown once, stored as its hash) and when it expires."""
    raw = "".join(secrets.choice(CODE_ALPHABET) for _ in range(CODE_LEN))
    expires = clock.now() + CODE_TTL
    await session.execute(_ISSUE, {"o": org, "u": user_id, "h": digest(raw), "exp": expires})
    return raw, expires


async def _as_self(session: AsyncSession, user_id: uuid.UUID) -> None:
    await lookup_as(await session.connection(), "app.self_user_id", str(user_id))


async def is_linked(session: AsyncSession, user_id: uuid.UUID) -> bool:
    await _as_self(session, user_id)
    return (await session.scalar(_CHAT, {"u": user_id})) is not None


async def unlink(session: AsyncSession, user_id: uuid.UUID) -> None:
    await _as_self(session, user_id)
    await session.execute(_SET_CHAT, {"u": user_id, "c": None})


class _Refused(Exception):
    def __init__(self, reply: str) -> None:
        self.reply = reply


async def redeem(
    engine: AsyncEngine, clock: Clock, from_id: int, chat_id: int, chat_type: str, code: str
) -> str:
    """Link the Telegram account `from_id` to the code's user; returns the bot's reply.

    Private chats only (a group would bind a chat many people can press in). The issuing organization must
    still have the flag; any refusal rolls back, so the code is not spent."""
    if chat_type != "private" or chat_id != from_id:
        return PRIVATE_ONLY
    if not _CODE.fullmatch(code):
        return BAD_CODE
    try:
        async with AsyncSession(engine) as s, s.begin():
            conn = await s.connection()
            await lookup_as(conn, "app.telegram_code_hash", digest(code))
            row = (await s.execute(_USE_CODE, {"h": digest(code), "now": clock.now()})).first()
            if row is None:
                return BAD_CODE
            org, user_id = row
            await use_tenant(conn, org)
            if not await features.is_enabled(s, org, FLAG):
                raise _Refused(NEEDS_PRO)
            await lookup_as(conn, "app.self_user_id", str(user_id))
            await s.execute(_SET_CHAT, {"u": user_id, "c": from_id})
    except _Refused as e:
        return e.reply
    except IntegrityError:  # the unique chat index: this account is someone else's
        return TAKEN
    return LINKED

"""Telegram linking and button presses (D19). Every press is authorized from the Telegram account that
pressed it, through the same decide_step as REST; nothing in the button names a step."""

import hashlib
import html
import re
import secrets
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.authz.matrix import Role
from app.authz.principals import UserPrincipal
from app.db.session import lookup_as, tenant_session, use_tenant
from app.domain.errors import AlreadyDecided, Forbidden, NotFoundError, StaleVersion, StepNotPending
from app.domain.receipt import KeyRing
from app.ports.clock import Clock
from app.services import features, steps

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


# --- approval prompts and their buttons ---------------------------------------------------------

NOT_VALID = "This button is no longer valid."
NOT_ALLOWED = "You may not decide this step."
# Callback data is `a:` or `r:` plus a random handle (16 bytes, base64url); only its hash is stored.
_DATA = re.compile(r"([ar]):([A-Za-z0-9_-]{22})")
_OUTCOME = {"approved": "Approved", "rejected": "Rejected", "timed_out": "Timed out",
            "superseded": "Superseded by a new version"}  # fmt: skip

_VIEW = text(
    "select s.status, s.level, s.request_version, d.title, d.effective_risk_level"
    " from approval_steps s join decision_requests d"
    " on d.organization_id = s.organization_id and d.id = s.decision_request_id"
    " where s.organization_id = :o and s.id = :s"
)
# Members who linked a chat; who of them may decide is decided by steps.pending_for, as for the inbox.
_LINKED = text(
    "select u.id, u.telegram_chat_id from memberships m join users u on u.id = m.user_id"
    " where m.organization_id = :o and u.telegram_chat_id is not null order by u.id"
)
_PROMPT = text(
    "insert into telegram_messages (organization_id, step_id, user_id, chat_id, handle_hash)"
    " values (:o, :s, :u, :c, :h) on conflict (step_id, user_id) do nothing returning id"
)
_SENT = text(
    "update telegram_messages set message_id = :m"
    " where organization_id = :o and id = :i and message_id is null"
)
_SHOWN = text(
    "select chat_id, message_id from telegram_messages"
    " where organization_id = :o and step_id = :s and message_id is not null"
)
_WHO = text("select id from users where telegram_chat_id = :c")
_HANDLE = text(
    "select organization_id, step_id, user_id, chat_id from telegram_messages where handle_hash = :h"
)


@dataclass(frozen=True)
class Prompt:
    row_id: uuid.UUID
    chat_id: int
    text: str
    buttons: list[tuple[str, str]]


@dataclass(frozen=True)
class PressResult:
    answer: str  # the callback answer the presser sees
    edit: str | None = None  # new text for the pressed message, once its step is decided


def prompt_text(view: Any) -> str:
    # Agent-written title is escaped for parse_mode HTML; description and payload never leave.
    return (
        f"<b>Approval needed</b> (level {view.level})\n{html.escape(view.title, quote=False)}\n"
        f"Risk: {html.escape(str(view.effective_risk_level))}"
    )


def outcome_text(view: Any) -> str:
    word = _OUTCOME.get(view.status, "No longer pending")
    return f"<b>{word}</b> (level {view.level})\n{html.escape(view.title, quote=False)}"


async def _view(session: AsyncSession, org: uuid.UUID, step_id: uuid.UUID) -> Any:
    return (await session.execute(_VIEW, {"o": org, "s": step_id})).first()


async def prompts(session: AsyncSession, org: uuid.UUID, step_id: uuid.UUID) -> list[Prompt]:
    """New prompts for a just-opened step: one per linked member who may decide it now.

    The row is the (step, user) marker: a re-run inserts none, so a prompt is sent at most once."""
    view = await _view(session, org, step_id)
    if (
        view is None
        or view.status != "pending"
        or not await features.is_enabled(session, org, FLAG)
    ):
        return []
    out = []
    for user_id, chat_id in (await session.execute(_LINKED, {"o": org})).all():
        if not any(r["id"] == step_id for r in await steps.pending_for(session, org, user_id)):
            continue  # the creator, their agent's owner, a lower role, another team
        handle = secrets.token_urlsafe(16)
        params = {"o": org, "s": step_id, "u": user_id, "c": chat_id, "h": digest(handle)}
        row = (await session.execute(_PROMPT, params)).first()
        if row is not None:
            buttons = [("Approve", f"a:{handle}"), ("Reject", f"r:{handle}")]
            out.append(Prompt(row[0], chat_id, prompt_text(view), buttons))
    return out


async def record_sent(
    session: AsyncSession, org: uuid.UUID, sent: list[tuple[uuid.UUID, int]]
) -> None:
    for row_id, message_id in sent:
        await session.execute(_SENT, {"o": org, "i": row_id, "m": message_id})


async def shown(
    session: AsyncSession, org: uuid.UUID, step_id: uuid.UUID
) -> list[tuple[int, int, str]]:
    """(chat, message, outcome text) for every prompt of a step that has left pending."""
    view = await _view(session, org, step_id)
    if (
        view is None
        or view.status == "pending"
        or not await features.is_enabled(session, org, FLAG)
    ):
        return []
    rows = (await session.execute(_SHOWN, {"o": org, "s": step_id})).all()
    return [(chat, message, outcome_text(view)) for chat, message in rows]


async def press(
    engine: AsyncEngine, clock: Clock, keys: KeyRing,
    from_id: int, chat_id: int, chat_type: str, data: object,
) -> PressResult:  # fmt: skip
    """Decide the step behind a button for the Telegram account that pressed it (I20).

    The handle only finds the prompt; the actor is the user linked to `from_id` now, who must be the one
    it was sent to, in their private chat. decide_step then reads role, team and duties afresh."""
    m = _DATA.fullmatch(data) if isinstance(data, str) else None
    if m is None or chat_type != "private" or chat_id != from_id:
        return PressResult(NOT_VALID)
    handle = digest(m.group(2))
    async with engine.begin() as c:
        await lookup_as(c, "app.telegram_chat_id", str(from_id))
        await lookup_as(c, "app.telegram_handle_hash", handle)
        user = (await c.execute(_WHO, {"c": from_id})).scalar()
        row = (await c.execute(_HANDLE, {"h": handle})).first()
    if user is None or row is None or row.user_id != user or row.chat_id != from_id:
        return PressResult(NOT_VALID)
    org, step_id, approve = row.organization_id, row.step_id, m.group(1) == "a"
    try:
        async with tenant_session(engine, org) as s:
            if not await features.is_enabled(s, org, FLAG):
                return PressResult(NEEDS_PRO)
            view = await _view(s, org, step_id)
            who = UserPrincipal(user, org, Role.MEMBER)  # the claimed role is never read
            out = await steps.decide_step(
                s, org, clock, keys=keys, who=who, step_id=step_id,
                request_version=view.request_version, approve=approve,
            )  # fmt: skip
        stale = out["replayed"]  # a second press: answered as stale, never applied again
    except (Forbidden, NotFoundError):
        return PressResult(NOT_ALLOWED)
    except (StaleVersion, StepNotPending, AlreadyDecided):
        stale = True
    async with tenant_session(engine, org) as s:
        view = await _view(s, org, step_id)
    if stale:
        return PressResult(f"Already decided: {_OUTCOME.get(view.status, 'no longer pending')}.",
                           outcome_text(view))  # fmt: skip
    return PressResult("Approved." if approve else "Rejected.", outcome_text(view))

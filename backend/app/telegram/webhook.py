"""The Bot API webhook (D19). Only Telegram knows the secret_token header; everything after it is still
untrusted input. A valid update always gets 200, else Telegram redelivers it; replies ride in the body."""

import hmac
import re
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, Request

from app.adapters.telegram_notifier import TelegramNotifier
from app.api.deps import ClockDep, EngineDep
from app.config import settings
from app.domain.errors import NotFoundError, Unauthenticated
from app.ports.notifier import Notifier
from app.services import telegram

router = APIRouter(tags=["telegram"])

_COMMAND = re.compile(r"/(start|link)(?:@\w+)?(?:\s+(\S+))?\s*")


def get_notifier() -> Notifier | None:
    return TelegramNotifier(settings.telegram_bot_token) if settings.telegram_bot_token else None


NotifierDep = Annotated[Notifier | None, Depends(get_notifier)]


def _int(v: Any) -> int | None:
    return v if type(v) is int else None  # a JSON true is not a chat id


def _verify(given: str | None) -> None:
    expected = settings.telegram_webhook_secret
    if not expected:
        raise NotFoundError  # not configured: the webhook does not exist
    if not hmac.compare_digest((given or "").encode(), expected.encode()):
        raise Unauthenticated


async def _message(engine, clock, msg: dict[str, Any]) -> dict[str, Any]:
    sender, chat = msg.get("from") or {}, msg.get("chat") or {}
    from_id, chat_id = _int(sender.get("id")), _int(chat.get("id"))
    words = msg.get("text")
    if from_id is None or chat_id is None or not isinstance(words, str):
        return {}
    cmd = _COMMAND.fullmatch(words)
    if cmd is None:
        return {}
    code = cmd.group(2)
    reply = (
        await telegram.redeem(engine, clock, from_id, chat_id, str(chat.get("type")), code)
        if code
        else "Open the link in Agent Ops settings to connect this chat."
    )
    return {"method": "sendMessage", "chat_id": chat_id, "text": reply}


@router.post("/telegram/webhook", include_in_schema=False)
async def webhook(
    request: Request,
    engine: EngineDep,
    clock: ClockDep,
    notifier: NotifierDep,
    x_telegram_bot_api_secret_token: Annotated[str | None, Header()] = None,
) -> dict[str, Any]:
    _verify(x_telegram_bot_api_secret_token)
    try:
        update = await request.json()
    except ValueError:
        return {}
    if not isinstance(update, dict):
        return {}
    if isinstance(msg := update.get("message"), dict):
        return await _message(engine, clock, msg)
    return {}

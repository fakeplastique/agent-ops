import asyncio
import logging
from typing import Any

import httpx2

TIMEOUT = 10.0  # seconds per Bot API call (spec 9)
_HOST = "api.telegram.org"


def _no_bot_urls(record: logging.LogRecord) -> bool:
    # httpx2 logs every request URL at INFO, and the Bot API puts the token in the path.
    return _HOST not in record.getMessage()


logging.getLogger("httpx2").addFilter(_no_bot_urls)


class TelegramNotifier:
    """The Bot API over HTTPS. The token sits in the URL path, so failures name a type, never a URL."""

    def __init__(
        self,
        token: str,
        transport: httpx2.AsyncBaseTransport | None = None,
        timeout: float = TIMEOUT,
    ) -> None:
        self.base = f"https://{_HOST}/bot{token}/"
        self.transport, self.timeout = transport, timeout

    async def _call(self, method: str, body: dict[str, Any]) -> Any:
        # One deadline for the whole call: httpx timeouts are per phase, so a trickling reply outlives them.
        async with asyncio.timeout(self.timeout):
            async with httpx2.AsyncClient(
                transport=self.transport, timeout=self.timeout, trust_env=False
            ) as c:
                r = await c.post(self.base + method, json=body)
            out = r.json()
        if not isinstance(out, dict) or out.get("ok") is not True:
            raise BotApiError(r.status_code)
        return out.get("result")

    async def send(self, chat_id: int, text: str, buttons: list[tuple[str, str]]) -> int:
        keyboard = [[{"text": label, "callback_data": data} for label, data in buttons]]
        body = {"chat_id": chat_id, "text": text, "parse_mode": "HTML",
                "reply_markup": {"inline_keyboard": keyboard}}  # fmt: skip
        return int((await self._call("sendMessage", body))["message_id"])

    async def edit(self, chat_id: int, message_id: int, text: str) -> None:
        body = {"chat_id": chat_id, "message_id": message_id, "text": text, "parse_mode": "HTML"}
        await self._call("editMessageText", body)


def configured() -> TelegramNotifier | None:
    """The bot from TELEGRAM_BOT_TOKEN; none configured means nothing is sent."""
    from app.config import settings

    return TelegramNotifier(settings.telegram_bot_token) if settings.telegram_bot_token else None


class BotApiError(Exception):
    def __init__(self, status: int) -> None:
        super().__init__(f"Bot API answered {status}")

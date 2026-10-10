"""S31 Telegram (D19): webhook auth, one-time link codes, buttons that act only for the linked user (I20, I13,
FM29, SC24). The Bot API is faked at the HTTP edge: the real adapter talks to an httpx2 MockTransport."""

import asyncio
import hashlib
import json
import logging
import re
import secrets
import sys
import uuid
from datetime import timedelta

import httpx2
import pytest

from app.adapters.telegram_notifier import TelegramNotifier
from app.config import settings
from app.main import app
from app.telegram import webhook as tg_webhook
from tests.integration.helpers import _exec, fetch, make_member, make_org
from tests.integration.web_harness import ORIGIN
from tests.security.test_token_hygiene import whole_database_text

pytestmark = pytest.mark.anyio

SECRET = "tg-secret_1"
HOOK = "/api/v1/telegram/webhook"
LINK = "/api/v1/me/telegram/link-code"
ME = "/api/v1/me/telegram"
CHAT = 0  # a fresh Telegram account per test (see `_fresh_chat`): users outlive a test


class FakeBot:
    """The Bot API at the HTTP edge: records every call, answers ok with a fresh message id."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []
        self.hang: set[int] = set()  # chat ids whose calls never answer
        self.next_id = 100

    async def handler(self, request: httpx2.Request) -> httpx2.Response:
        method = request.url.path.rsplit("/", 1)[1]
        body = json.loads(request.content)
        self.calls.append((method, body))
        if body.get("chat_id") in self.hang:
            await asyncio.sleep(3600)
        self.next_id += 1
        return httpx2.Response(200, json={"ok": True, "result": {"message_id": self.next_id}})

    def notifier(self, timeout: float = 10.0) -> TelegramNotifier:
        return TelegramNotifier("123:TESTTOKEN", httpx2.MockTransport(self.handler), timeout)

    def of(self, method: str) -> list[dict]:
        return [b for m, b in self.calls if m == method]


@pytest.fixture(autouse=True)
def _fresh_chat(monkeypatch):
    monkeypatch.setattr(sys.modules[__name__], "CHAT", 10**9 + secrets.randbelow(10**9))


@pytest.fixture
async def tg(web, monkeypatch):
    monkeypatch.setattr(settings, "telegram_webhook_secret", SECRET)
    monkeypatch.setattr(settings, "telegram_bot_username", "agentops_bot")
    bot = FakeBot()
    app.dependency_overrides[tg_webhook.get_notifier] = bot.notifier
    return web, bot


def message(text: str, from_id: int | None = None, chat_type="private", chat_id=None) -> dict:
    from_id = from_id or CHAT
    who = {"id": from_id, "is_bot": False, "first_name": "T"}
    chat = {"id": chat_id or from_id, "type": chat_type}
    return {"update_id": 1, "message": {"message_id": 1, "from": who, "chat": chat, "text": text}}


async def hook(web, update: dict, secret: str | None = SECRET):
    headers = {} if secret is None else {"X-Telegram-Bot-Api-Secret-Token": secret}
    return await web.client.post(HOOK, json=update, headers=headers)


async def signed_in(web, owner, org, role="manager", team=None) -> uuid.UUID:
    """A browser session of a new user who is a member of `org` with `role`, the org selected."""
    email = f"u-{uuid.uuid4()}@example.test"
    await web.login(email)
    uid = (await fetch(owner, "select id from users where email=:e", e=email))[0][0]
    await make_member(owner, org, uid, role, team)
    r = await web.post("/api/v1/auth/organization", json={"organization_id": str(org)})
    assert r.status_code == 200, r.text
    return uid


async def chat_of(owner, user) -> int | None:
    return (await fetch(owner, "select telegram_chat_id from users where id=:u", u=user))[0][0]


async def new_code(web) -> str:
    r = await web.post(LINK)
    assert r.status_code == 201, r.text
    return r.json()["code"]


async def test_missing_or_wrong_secret_token_header_rejected(tg, owner_engine, monkeypatch):
    web, _ = tg
    org = await make_org(owner_engine, "pro")
    user = await signed_in(web, owner_engine, org)
    code = await new_code(web)
    for bad in (None, "", "wrong", SECRET + "x", SECRET.upper()):
        r = await hook(web, message(f"/start {code}"), bad)
        assert r.status_code == 401, bad  # catches: anyone posting fake updates
    assert await chat_of(owner_engine, user) is None
    monkeypatch.setattr(settings, "telegram_webhook_secret", "")
    assert (await hook(web, message(f"/start {code}"), "")).status_code == 404  # unset = off
    monkeypatch.setattr(settings, "telegram_webhook_secret", SECRET)
    r = await hook(web, message(f"/start {code}"))
    assert r.status_code == 200 and await chat_of(owner_engine, user) == CHAT  # code was unspent


async def test_link_code_single_use_10min_8_chars_hashed_private_chat_only(
    tg, owner_engine, caplog
):
    caplog.set_level(logging.DEBUG)
    web, _ = tg
    org = await make_org(owner_engine, "pro")
    user = await signed_in(web, owner_engine, org)
    r = await web.post(LINK)
    assert r.status_code == 201, r.text
    out = r.json()
    code = out["code"]
    assert re.fullmatch(r"[A-HJ-NP-Z2-9]{8}", code)
    assert out["link"] == f"https://t.me/agentops_bot?start={code}"
    assert out["expires_at"].startswith((web.clock.now() + timedelta(minutes=10)).isoformat()[:19])
    assert (await web.client.get(ME)).json() == {"linked": False}

    # A group chat (or a channel post relayed with another chat) links nothing and spends nothing.
    for upd in (
        message(f"/start {code}", chat_type="group", chat_id=-100),
        message(f"/start {code}", chat_type="supergroup", chat_id=-100),
        message(f"/start {code}", chat_id=CHAT + 1),
    ):
        r = await hook(web, upd)
        assert r.status_code == 200 and "private" in r.json()["text"]
    assert await chat_of(owner_engine, user) is None

    r = await hook(web, message(f"/link {code}"))
    assert r.json()["method"] == "sendMessage" and r.json()["chat_id"] == CHAT
    assert await chat_of(owner_engine, user) == CHAT
    assert (await web.client.get(ME)).json() == {"linked": True}
    # Single use: the same code from another account links nothing.
    r = await hook(web, message(f"/start {code}", from_id=CHAT + 5))
    assert "invalid or expired" in r.json()["text"]
    assert await chat_of(owner_engine, user) == CHAT

    # Ten minutes, then dead.
    late = await new_code(web)
    web.clock.at += timedelta(minutes=10, seconds=1)
    r = await hook(web, message(f"/start {late}", from_id=CHAT + 6))
    assert "invalid or expired" in r.json()["text"]
    assert not await fetch(
        owner_engine, "select 1 from users where telegram_chat_id=:c", c=CHAT + 6
    )

    # Hashed at rest, never logged (I13).
    haystack = await whole_database_text(owner_engine) + caplog.text
    assert code not in haystack and late not in haystack
    digests = {r[0] for r in await fetch(owner_engine, "select code_hash from telegram_link_codes")}
    assert hashlib.sha256(code.encode()).hexdigest() in digests

    # A Telegram account linked to one user cannot be taken by another.
    other = await signed_in(web, owner_engine, org)
    r = await hook(web, message(f"/start {await new_code(web)}"))
    assert "already linked" in r.json()["text"]
    assert await chat_of(owner_engine, other) is None and await chat_of(owner_engine, user) == CHAT

    # Unlink from the web: the account is free again.
    await web.login(
        (await fetch(owner_engine, "select email from users where id=:u", u=user))[0][0]
    )
    await web.post("/api/v1/auth/organization", json={"organization_id": str(org)})
    assert (await web.client.delete(ME, headers={"Origin": ORIGIN})).status_code == 204
    assert await chat_of(owner_engine, user) is None


async def test_free_plan_cannot_link_or_receive(tg, owner_engine):
    web, _ = tg
    org = await make_org(owner_engine, "free")
    user = await signed_in(web, owner_engine, org)
    r = await web.post(LINK)
    assert r.status_code == 402 and r.json()["error"]["code"] == "plan_feature_required"
    # A code issued on Pro is refused once the organization drops to Free before it is used.
    pro = await make_org(owner_engine, "pro")
    await make_member(owner_engine, pro, user, "manager")
    await web.post("/api/v1/auth/organization", json={"organization_id": str(pro)})
    code = await new_code(web)
    await _exec(owner_engine, "update organizations set plan_key='free' where id=:o", o=pro)
    r = await hook(web, message(f"/start {code}"))
    assert "Pro" in r.json()["text"] and await chat_of(owner_engine, user) is None

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
from tests.integration.helpers import (
    _exec,
    approval_world,
    cast,
    decide,
    fetch,
    make_member,
    make_org,
    make_world,
    open_request,
    pending_step,
    staff,
    voters,
)
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


async def hook(web, update: object, secret: str | None = SECRET):
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

    r = await hook(web, message(f"/link {code.lower()}"))  # typed by hand
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


async def test_free_plan_receives_nothing(tg, owner_engine, app_engine):
    _, bot = tg
    w, _req, step, _, _ = await approvers(owner_engine, app_engine, plan="free")
    assert await prompt(app_engine, owner_engine, w, step, bot) == 0  # catches: ungated delivery
    assert bot.calls == [] and not await fetch(owner_engine, "select 1 from telegram_messages"
                                                " where organization_id=:o", o=w.org)  # fmt: skip
    # Prompted on Pro, then downgraded: the outcome is not delivered either.
    w, _req, step, (m,), _ = await approvers(owner_engine, app_engine, 1)
    assert await prompt(app_engine, owner_engine, w, step, bot) == 1
    await _exec(owner_engine, "update organizations set plan_key='free' where id=:o", o=w.org)
    await decide(app_engine, w, step, m)
    assert await outcome(app_engine, owner_engine, w, step, bot) == 0
    assert bot.of("editMessageText") == []


async def outcome(app_engine, owner, w, step, bot: FakeBot) -> int:
    """Run the arq `notifications` consumer on the step's step.decided event."""
    from app.worker.notify import notifications

    ((event,),) = await fetch(
        owner, "select id from outbox_events where type='step.decided' and payload->>'id'=:s",
        s=str(step),
    )  # fmt: skip
    ctx = {"work_engine": app_engine, "notifier": bot.notifier()}
    return await notifications(ctx, str(w.org), str(event))


# --- buttons ---------------------------------------------------------------------------------------

ONE_LEVEL = [{"role": "manager", "timeout_minutes": 60}]


def press(
    data, from_id: int | None = None, chat_type="private", chat_id=None, message_id=7
) -> dict:
    from_id = from_id or CHAT
    who = {"id": from_id, "is_bot": False, "first_name": "T"}
    chat = {"id": chat_id or from_id, "type": chat_type}
    msg = {"message_id": message_id, "chat": chat}
    return {
        "update_id": 2,
        "callback_query": {"id": "cq", "from": who, "message": msg, "data": data},
    }


async def link(owner, user) -> int:
    """Link `user` to a fresh Telegram account (the link flow itself is tested above)."""
    chat = 10**9 + secrets.randbelow(10**9)
    await _exec(owner, "update users set telegram_chat_id=:c where id=:u", c=chat, u=user)
    return chat


async def step_created_event(owner, step) -> uuid.UUID:
    rows = await fetch(
        owner, "select id from outbox_events where type='step.created' and payload->>'id'=:s",
        s=str(step),
    )  # fmt: skip
    return rows[0][0]


async def prompt(app_engine, owner, w, step, bot: FakeBot, timeout: float = 10.0) -> int:
    from app.worker.notify import notify

    event = await step_created_event(owner, step)
    return await notify(app_engine, w.org, event, bot.notifier(timeout))


def buttons_for(bot: FakeBot, chat: int) -> dict[str, str]:
    """label -> callback data of the prompt sent to `chat`."""
    sent = [b for b in bot.of("sendMessage") if b["chat_id"] == chat]
    assert len(sent) == 1, sent
    row = sent[0]["reply_markup"]["inline_keyboard"][0]
    return {b["text"]: b["callback_data"] for b in row}


async def state(owner, w) -> list:
    """Everything a press could change: steps, request status, audit and outbox rows of the org."""
    return [
        await fetch(owner, "select id, status, approver_user_id from approval_steps"
                    " where organization_id=:o order by id", o=w.org),
        await fetch(owner, "select status, receipt from decision_requests where organization_id=:o",
                    o=w.org),
        await fetch(owner, "select count(*) from audit_logs where organization_id=:o", o=w.org),
        await fetch(owner, "select count(*) from outbox_events where organization_id=:o", o=w.org),
    ]  # fmt: skip


async def approvers(owner, app_engine, n=2, plan="pro"):
    """A request waiting on one manager step, and n linked managers (users, chats)."""
    w, req, _ = await approval_world(owner, app_engine, levels=ONE_LEVEL, plan=plan)
    step, *_ = await pending_step(owner, req)
    people = [await staff(owner, w.org, "manager") for _ in range(n)]
    return w, req, step, people, [await link(owner, p) for p in people]


async def test_press_by_unlinked_or_different_user_does_nothing(tg, owner_engine, app_engine):
    web, bot = tg
    w, req, step, (m, _n), (cm, cn) = await approvers(owner_engine, app_engine)
    assert await prompt(app_engine, owner_engine, w, step, bot) == 2
    approve = buttons_for(bot, cm)["Approve"]
    before = await state(owner_engine, w)
    for upd in (
        press(approve, from_id=cm + 1),  # an account nobody linked
        press(approve, from_id=cn),  # another linked approver who may decide this very step
        press(approve, from_id=cm, chat_type="group", chat_id=-100),  # the right person, in a group
        press(approve, from_id=cm, chat_id=cn),  # the right person, someone else's chat
    ):
        r = await hook(web, upd)
        assert r.status_code == 200 and r.json()["method"] == "answerCallbackQuery"
        assert await state(owner_engine, w) == before, upd  # catches: trusting the handle alone
    # The Telegram account now belongs to another (eligible) user: the old prompt is not theirs.
    third = await staff(owner_engine, w.org, "manager")
    await _exec(owner_engine, "update users set telegram_chat_id=null where id=:u", u=m)
    await _exec(owner_engine, "update users set telegram_chat_id=:c where id=:u", c=cm, u=third)
    r = await hook(web, press(approve, from_id=cm))
    assert "no longer valid" in r.json()["text"] and await state(owner_engine, w) == before
    await _exec(owner_engine, "update users set telegram_chat_id=null where id=:u", u=third)
    await _exec(owner_engine, "update users set telegram_chat_id=:c where id=:u", c=cm, u=m)
    assert bot.of("editMessageText") == []

    # SC24: the linked approver's own press decides through decide_step (receipt and all).
    r = await hook(web, press(approve, from_id=cm))
    assert r.json() == {
        "method": "answerCallbackQuery", "callback_query_id": "cq", "text": "Approved.",
    }  # fmt: skip
    ((status, approver),) = await fetch(
        owner_engine, "select status, approver_user_id from approval_steps where id=:s", s=step
    )
    assert (status, approver) == ("approved", m)
    ((req_status, receipt),) = await fetch(
        owner_engine, "select status, receipt from decision_requests where id=:r", r=req["id"]
    )
    assert req_status == "approved" and receipt["payload"]["approvers"] == [str(m)]
    edited = bot.of("editMessageText")
    assert [e["chat_id"] for e in edited] == [cm] and "Approved" in edited[0]["text"]


async def test_press_after_role_change_or_by_creator_is_refused(tg, owner_engine, app_engine):
    web, bot = tg
    w, _req, step, (m,), (cm,) = await approvers(owner_engine, app_engine, 1)
    # The agent's owner may never decide its request: not prompted, and a planted button is refused.
    await _exec(
        owner_engine, "update memberships set role='manager' where user_id=:u", u=w.owner_user
    )
    creator_chat = await link(owner_engine, w.owner_user)
    assert await prompt(app_engine, owner_engine, w, step, bot) == 1
    assert [b["chat_id"] for b in bot.of("sendMessage")] == [cm]
    handle = "C" * 22
    await _exec(
        owner_engine,
        "insert into telegram_messages (organization_id, step_id, user_id, chat_id, handle_hash)"
        " values (:o, :s, :u, :c, :h)",
        o=w.org, s=step, u=w.owner_user, c=creator_chat,
        h=hashlib.sha256(handle.encode()).hexdigest(),
    )  # fmt: skip
    before = await state(owner_engine, w)
    r = await hook(web, press(f"a:{handle}", from_id=creator_chat))
    assert "may not decide" in r.json()["text"] and await state(owner_engine, w) == before

    # Demoted after the prompt went out: the role is read at press time, not send time.
    approve = buttons_for(bot, cm)["Approve"]
    await _exec(owner_engine, "update memberships set role='member' where user_id=:u", u=m)
    r = await hook(web, press(approve, from_id=cm))
    assert "may not decide" in r.json()["text"]  # catches: role cached at send time
    assert await state(owner_engine, w) == before
    # Removed from the organization: same answer, nothing changes.
    await _exec(owner_engine, "delete from memberships where user_id=:u", u=m)
    assert "may not decide" in (await hook(web, press(approve, from_id=cm))).json()["text"]
    # The organization lost the plan: Telegram cannot act for it any more.
    await make_member(owner_engine, w.org, m, "manager")
    await _exec(owner_engine, "update organizations set plan_key='free' where id=:o", o=w.org)
    assert "Pro" in (await hook(web, press(approve, from_id=cm))).json()["text"]
    assert await state(owner_engine, w) == before


async def test_stale_press_replies_409_edits_message_and_changes_nothing(
    tg, owner_engine, app_engine
):
    web, bot = tg
    w, _req, step, (_m, n), (cm, cn) = await approvers(owner_engine, app_engine)
    await prompt(app_engine, owner_engine, w, step, bot)
    await decide(app_engine, w, step, n)  # decided elsewhere (REST, the web app)
    before = await state(owner_engine, w)
    for label in ("Approve", "Reject"):
        r = await hook(web, press(buttons_for(bot, cm)[label], from_id=cm, message_id=55))
        assert r.status_code == 200 and "already decided" in r.json()["text"].lower()
        assert await state(owner_engine, w) == before  # catches: a decision applied twice
    edits = bot.of("editMessageText")
    assert {(e["chat_id"], e["message_id"]) for e in edits} == {(cm, 55)}
    assert all("Approved" in e["text"] for e in edits)
    # The decider's own second press is stale too: never a reversal, never a second success.
    for label in ("Reject", "Approve"):
        r = await hook(web, press(buttons_for(bot, cn)[label], from_id=cn))
        assert "already decided" in r.json()["text"].lower()
        assert await state(owner_engine, w) == before


async def test_callback_data_is_opaque_and_tampered_data_ignored(tg, owner_engine, app_engine):
    web, bot = tg
    w, req, step, _, (cm, _cn) = await approvers(owner_engine, app_engine)
    await prompt(app_engine, owner_engine, w, step, bot)
    data = buttons_for(bot, cm)
    for d in data.values():
        assert len(d.encode()) <= 64  # the Bot API's limit
        for ident in (step, req["id"], w.org):
            assert (
                str(ident) not in d and ident.hex not in d
            )  # catches: step id forged in the button
    handle = data["Approve"][2:]
    flipped = handle[:-1] + ("A" if handle[-1] != "A" else "B")
    before = await state(owner_engine, w)
    for bad in (
        f"a:{flipped}", f"a:{step}", f"x:{handle}", "a:", "", f"a:{handle}x", 42, None,
        "a:" + "A" * 200, data["Reject"].replace("r:", "a:", 1) + " ",
    ):  # fmt: skip
        r = await hook(web, press(bad, from_id=cm))
        assert r.status_code == 200 and "no longer valid" in r.json()["text"], bad
    assert await state(owner_engine, w) == before and bot.of("editMessageText") == []
    for junk in ([], "x", {"callback_query": "x"}, {"callback_query": {"data": "a:" + handle}}):
        assert (await hook(web, junk)).status_code == 200  # never 5xx: Telegram would redeliver
    assert await state(owner_engine, w) == before


# --- messages --------------------------------------------------------------------------------------


async def test_title_markdown_html_escaped_and_no_payload_sent(
    tg, owner_engine, app_engine, caplog
):
    caplog.set_level(logging.DEBUG)
    _, bot = tg
    w = await make_world(owner_engine, "pro", levels=ONE_LEVEL, floors={"refund": "medium"})
    title = '<b>Pay</b> *now* [x](https://evil.test) & <a href="tg://x">y</a>'
    req = await open_request(
        app_engine, w, title=title, description="DESC-SENTINEL", payload={"k": "PAYLOAD-SENTINEL"}
    )
    for s in await voters(owner_engine, w, 3):
        await cast(app_engine, w, req, s)
    step, *_ = await pending_step(owner_engine, req)
    m = await staff(owner_engine, w.org, "manager")
    cm = await link(owner_engine, m)
    assert await prompt(app_engine, owner_engine, w, step, bot) == 1
    (sent,) = bot.of("sendMessage")
    assert sent["parse_mode"] == "HTML" and sent["chat_id"] == cm
    assert "&lt;b&gt;Pay&lt;/b&gt;" in sent["text"] and "&lt;a href" in sent["text"]
    assert "<b>Pay" not in sent["text"] and "<a " not in sent["text"]  # catches: agent markup
    assert "medium" in sent["text"] and "level 1" in sent["text"]
    everything = json.dumps(bot.calls)
    assert "SENTINEL" not in everything  # catches: description or payload leaking to Telegram
    ((mid,),) = await fetch(
        owner_engine, "select message_id from telegram_messages where step_id=:s", s=step
    )
    assert mid == bot.next_id

    # The step is decided elsewhere: the consumer edits every prompt of it to the outcome.
    await decide(app_engine, w, step, m, approve=False)
    assert await outcome(app_engine, owner_engine, w, step, bot) == 1
    (edit,) = bot.of("editMessageText")
    assert (edit["chat_id"], edit["message_id"]) == (cm, mid) and "Rejected" in edit["text"]
    assert "&lt;b&gt;Pay" in edit["text"] and "reply_markup" not in edit
    assert "SENTINEL" not in json.dumps(bot.calls)
    # The bot token rides in the Bot API URL: no log line may carry it (I13).
    assert bot.calls and "TESTTOKEN" not in caplog.text


async def test_telegram_timeout_10s_does_not_block_worker(tg, owner_engine, app_engine):
    from app.adapters import telegram_notifier

    _, bot = tg
    assert telegram_notifier.TIMEOUT == 10.0 and TelegramNotifier("t").timeout == 10.0
    w, _req, step, _people, chats = await approvers(owner_engine, app_engine, 4)
    bot.hang = set(chats[:3])  # three chats whose Bot API calls never answer
    started = asyncio.get_running_loop().time()
    assert await prompt(app_engine, owner_engine, w, step, bot, timeout=0.5) == 1
    # Side by side, each cut at its deadline: three hangs cost one timeout, not three.
    assert asyncio.get_running_loop().time() - started < 1.4
    rows = dict(await fetch(
        owner_engine, "select chat_id, message_id from telegram_messages where step_id=:s", s=step
    ))  # fmt: skip
    assert rows[chats[3]] is not None and all(rows[c] is None for c in chats[:3])
    # A hanging edit is cut the same way and the press still answers.
    web, _ = tg
    app.dependency_overrides[tg_webhook.get_notifier] = lambda: bot.notifier(0.5)
    approve = buttons_for(bot, chats[3])["Approve"]
    bot.hang.add(chats[3])
    r = await hook(web, press(approve, from_id=chats[3]))
    assert r.json()["text"] == "Approved."

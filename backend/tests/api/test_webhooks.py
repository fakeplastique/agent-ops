"""Webhook subscriptions over REST (S27): org_admin only, SSRF-checked URLs, secret shown once (I13, FM20, FM21)."""

import asyncio
import base64
import logging
import re
from pathlib import Path

import pytest

from app.api.v1 import webhooks as api
from app.config import settings
from app.db.session import tenant_session
from app.domain.errors import InvalidInput
from app.domain.secretbox import SealError, SecretBox
from app.main import app
from app.services import webhook_security as ws
from app.services import webhooks
from tests.api.harness import agent_auth, user_auth
from tests.integration.helpers import fetch, make_world, staff
from tests.security.test_token_hygiene import whole_database_text

pytestmark = pytest.mark.anyio

WH = "/api/v1/webhook-subscriptions"
PUBLIC, INTERNAL_IP = "93.184.215.14", "10.11.12.13"
BODY = {"url": "https://hooks.example.com/agent-ops", "events": ["decision.created"]}


def _dns(table: dict[str, str]):
    async def resolve(host: str, port: int) -> list[str]:
        return [table.get(host, PUBLIC)]

    app.dependency_overrides[api.get_resolver] = lambda: resolve


async def _admin(app_engine, owner_engine):
    w = await make_world(owner_engine)
    return w, await user_auth(app_engine, w.org, await staff(owner_engine, w.org, "org_admin"))


async def test_subscription_crud(web, app_engine, owner_engine):
    _dns({})
    _, admin = await _admin(app_engine, owner_engine)
    r = await web.client.post(
        WH, json=BODY | {"url": "https://Hooks.Example.com/a#x"}, headers=admin
    )
    assert r.status_code == 201, r.text
    sub = r.json()
    one = f"{WH}/{sub['id']}"
    assert r.headers["location"] == one
    assert sub["url"] == "https://hooks.example.com/a" and sub["active"] is True
    got = await web.client.get(one, headers=admin)
    assert got.status_code == 200 and "secret" not in got.json()
    for i in range(2):
        await web.client.post(WH, json=BODY, headers=admin)
    page = (await web.client.get(f"{WH}?limit=2", headers=admin)).json()
    assert page["total"] == 3 and len(page["items"]) == 2 and "skip=2" in page["nextLink"]
    patched = await web.client.patch(
        one, json={"events": ["vote.cast", "decision.created", "vote.cast"], "active": False},
        headers=admin,
    )  # fmt: skip
    assert patched.status_code == 200, patched.text
    assert patched.json()["events"] == ["decision.created", "vote.cast"]
    assert patched.json()["active"] is False and patched.json()["url"] == sub["url"]
    for bad in (
        {"events": ["decision.exploded"]},
        {"events": []},
        {"url": None},
        {"active": "yes"},
        {"secret": "whsec_x"},
    ):
        assert (await web.client.patch(one, json=bad, headers=admin)).status_code == 422, bad
    assert (await web.client.delete(one, headers=admin)).status_code == 204
    assert (await web.client.get(one, headers=admin)).status_code == 404
    assert (await web.client.post(f"{one}/rotate-secret", headers=admin)).status_code == 404


async def test_blocked_url_error_names_the_rule_not_the_resolved_ip(web, app_engine, owner_engine):
    _dns({"intranet.example.com": INTERNAL_IP})
    _, admin = await _admin(app_engine, owner_engine)
    r = await web.client.post(
        WH, json=BODY | {"url": "https://intranet.example.com/h"}, headers=admin
    )
    assert r.status_code == 422
    assert r.json()["error"] == {
        "code": "webhook_url_refused",
        "message": "webhook URL refused: private address range",
        "details": {"rule": "private address range"},
    }
    assert (
        INTERNAL_IP not in r.text
    )  # catches: an error that maps the internal network for the caller
    r = await web.client.post(WH, json=BODY | {"url": "https://0x7f.1/h"}, headers=admin)
    assert r.status_code == 422 and "127.0.0.1" not in r.text
    # PATCH re-runs the guard: a saved public hook cannot be repointed inside
    sub = (await web.client.post(WH, json=BODY, headers=admin)).json()
    for url in (
        "https://intranet.example.com/h",
        "https://169.254.169.254/",
        "http://hooks.example.com/",
    ):
        r = await web.client.patch(f"{WH}/{sub['id']}", json={"url": url}, headers=admin)
        assert r.status_code == 422 and r.json()["error"]["code"] == "webhook_url_refused", url
    assert (await web.client.get(f"{WH}/{sub['id']}", headers=admin)).json()["url"] == sub["url"]


async def test_org_admin_only_and_cross_tenant_404(web, app_engine, owner_engine):
    _dns({})
    w, admin = await _admin(app_engine, owner_engine)
    sub = (await web.client.post(WH, json=BODY, headers=admin)).json()
    one = f"{WH}/{sub['id']}"
    calls = [
        ("POST", WH, BODY), ("GET", WH, None), ("GET", one, None),
        ("PATCH", one, {"active": False}), ("POST", f"{one}/rotate-secret", None), ("DELETE", one, None),
    ]  # fmt: skip
    for role in ("member", "team_lead", "manager"):
        h = await user_auth(app_engine, w.org, await staff(owner_engine, w.org, role))
        for method, path, body in calls:  # catches: a member creating or reading hooks
            r = await web.client.request(method, path, json=body, headers=h)
            assert r.status_code == 403, (role, method, path)
    agent = await agent_auth(app_engine, w.org, w.identity)
    for method, path, body in calls:
        assert (await web.client.request(method, path, json=body, headers=agent)).status_code == 403
    _, b_admin = await _admin(app_engine, owner_engine)
    for method, path, body in calls[2:]:
        r = await web.client.request(method, path, json=body, headers=b_admin)
        assert r.status_code == 404, (method, path)
    assert (await web.client.get(WH, headers=b_admin)).json()["total"] == 0
    after = (await web.client.get(one, headers=admin)).json()
    sub.pop("secret")
    assert after == sub  # nothing of A changed: not even updated_at


async def test_secret_returned_once_stored_as_aes_gcm_ciphertext_and_absent_from_logs(
    web, app_engine, owner_engine, caplog
):
    caplog.set_level(logging.DEBUG)
    _dns({})
    w, admin = await _admin(app_engine, owner_engine)
    r = await web.client.post(WH, json=BODY, headers=admin)
    secret, sub = r.json()["secret"], r.json()["id"]
    assert re.fullmatch(r"whsec_[A-Za-z0-9+/]{43}=", secret)
    seen = [
        (await web.client.get(f"{WH}/{sub}", headers=admin)).text,
        (await web.client.get(WH, headers=admin)).text,
        (await web.client.patch(f"{WH}/{sub}", json={"active": False}, headers=admin)).text,
        (await web.client.get("/api/v1/audit-logs", headers=admin)).text,
    ]
    [(stored,)] = await fetch(
        owner_engine, "select secret_ciphertext from webhook_subscriptions where id=:i", i=sub
    )
    key_part = secret.removeprefix("whsec_")
    haystack = "\n".join([*seen, await whole_database_text(owner_engine), caplog.text])
    for needle in (secret, key_part, base64.b64decode(key_part).hex()):
        assert needle not in haystack  # catches: the secret readable after creation, or logged
    # AES-GCM under the configured key, bound to this row: it opens here and nowhere else
    box = SecretBox.parse(settings.webhook_secret_keys)
    assert stored.startswith(f"{box.current}.")
    assert box.open(stored, f"webhook_subscription:{w.org}:{sub}".encode()) == secret
    with pytest.raises(SealError):
        box.open(stored, f"webhook_subscription:{w.org}:{w.policy}".encode())
    async with tenant_session(app_engine, w.org) as s:
        assert await webhooks.load_secret(s, w.org, sub) == secret  # what delivery (S28) signs with


async def test_rotate_returns_new_secret_once(web, app_engine, owner_engine, caplog):
    caplog.set_level(logging.DEBUG)
    _dns({})
    w, admin = await _admin(app_engine, owner_engine)
    created = (await web.client.post(WH, json=BODY, headers=admin)).json()
    r = await web.client.post(f"{WH}/{created['id']}/rotate-secret", headers=admin)
    assert r.status_code == 200 and r.json()["id"] == created["id"]
    new = r.json()["secret"]
    assert new != created["secret"] and new.startswith("whsec_")
    async with tenant_session(app_engine, w.org) as s:
        live = await webhooks.load_secret(s, w.org, created["id"])
    assert live == new
    body, ts = b'{"type":"decision.created"}', 1_700_000_000
    sig = ws.sign(live, "msg_1", ts, body)  # what the next delivery carries
    assert ws.verify(new, "msg_1", ts, body, sig, now=ts)
    assert not ws.verify(created["secret"], "msg_1", ts, body, sig, now=ts)  # the old one stops
    got = (await web.client.get(f"{WH}/{created['id']}", headers=admin)).text
    for s in (new, created["secret"]):
        assert s.removeprefix("whsec_") not in got + caplog.text + await whole_database_text(
            owner_engine
        )


async def test_lone_surrogate_url_is_a_clean_422(web, app_engine, owner_engine):
    _dns({})
    _, admin = await _admin(app_engine, owner_engine)
    raw = b'{"url": "https://hooks.example.com/\\ud800", "events": ["vote.cast"]}'
    h = admin | {"content-type": "application/json"}
    r = await web.client.post(WH, content=raw, headers=h)
    assert r.status_code == 422, (
        r.text
    )  # pydantic refuses it first; the guard refuses it too (table)


async def test_event_filter_and_per_org_cap(web, app_engine, owner_engine, monkeypatch):
    _dns({})
    _, admin = await _admin(app_engine, owner_engine)
    for events in ([], ["*"], ["decision.created", "nope"], "decision.created", [1]):
        r = await web.client.post(WH, json=BODY | {"events": events}, headers=admin)
        assert r.status_code == 422, events
    monkeypatch.setattr(webhooks, "MAX_SUBSCRIPTIONS", 2)
    codes = [(await web.client.post(WH, json=BODY, headers=admin)).status_code for _ in range(3)]
    assert codes == [201, 201, 422]


def test_every_emitted_event_type_can_be_subscribed():
    # catches: a new outbox event no subscriber can select (the filter list drifting from the engine)
    src = "\n".join(p.read_text() for p in Path(webhooks.__file__).parent.rglob("*.py"))
    emitted = set(re.findall(r'outbox\.emit\(\s*\w+,\s*\w+,\s*"([a-z_]+\.[a-z_]+)"', src))
    assert emitted == webhooks.EVENT_TYPES, emitted ^ webhooks.EVENT_TYPES  # and none stale


async def test_two_concurrent_creates_cannot_both_pass_the_cap(
    app_engine, owner_engine, monkeypatch
):
    monkeypatch.setattr(webhooks, "MAX_SUBSCRIPTIONS", 1)
    w = await make_world(owner_engine)

    async def create():
        async with tenant_session(app_engine, w.org) as s:
            await webhooks.create_subscription(s, w.org, w.owner_user, BODY["url"], BODY["events"])

    async with tenant_session(app_engine, w.org) as s1:
        await webhooks.create_subscription(s1, w.org, w.owner_user, BODY["url"], BODY["events"])
        second = asyncio.create_task(create())
        await asyncio.sleep(0.5)  # the second create counts while the first is still uncommitted
    with pytest.raises(InvalidInput):
        await second  # catches: read-then-write cap check letting both through
    rows = await fetch(
        owner_engine, "select count(*) from webhook_subscriptions where organization_id=:o", o=w.org
    )
    assert rows == [(1,)]


async def test_dns_is_resolved_before_any_transaction_opens(web, app_engine, owner_engine):
    # catches: a slow resolver holding a DB connection and transaction open (up to the DNS timeout)
    seen: list[int] = []

    async def resolve(host: str, port: int) -> list[str]:
        [(n,)] = await fetch(
            owner_engine,
            "select count(*) from pg_stat_activity"
            " where usename = 'app_rt' and datname = current_database()"
            " and state like 'idle in transaction%'",
        )
        seen.append(n)
        return [PUBLIC]

    app.dependency_overrides[api.get_resolver] = lambda: resolve
    _, admin = await _admin(app_engine, owner_engine)
    sub = (await web.client.post(WH, json=BODY, headers=admin)).json()
    r = await web.client.patch(f"{WH}/{sub['id']}", json={"url": BODY["url"]}, headers=admin)
    assert r.status_code == 200, r.text
    assert seen == [0, 0]

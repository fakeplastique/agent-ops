from datetime import timedelta

import pytest
from sqlalchemy import text

from app.config import settings
from tests.api.harness import user_auth
from tests.integration.helpers import (
    NOW,
    approval_world,
    cast,
    decide,
    fetch,
    make_world,
    open_request,
    pending_step,
    staff,
    timeouts,
    voters,
    withdraw,
)

pytestmark = pytest.mark.anyio

DR = "/api/v1/decision-requests"
VERIFY = "/api/v1/receipts/verify"
ONE_LEVEL = [{"role": "manager", "timeout_minutes": 30}]


@pytest.fixture(autouse=True)
def _helper_keys(monkeypatch):
    # The helpers sign with KEYS; the app must verify with the same ring.
    monkeypatch.setattr(settings, "receipt_keys", f"t1:{'k' * 40}")


async def human_approved(owner, app_engine):
    """A medium request approved by one manager: (world, request, manager)."""
    w, req, _ = await approval_world(owner, app_engine, levels=ONE_LEVEL)
    step, *_ = await pending_step(owner, req)
    manager = await staff(owner, w.org, "manager")
    await decide(app_engine, w, step, manager)
    return w, req, manager


async def auto_approved(owner, app_engine):
    w = await make_world(owner, floors={"refund": "low"})
    req = await open_request(app_engine, w)
    for s in await voters(owner, w, 3):
        await cast(app_engine, w, req, s)
    return w, req


async def receipt_of(web, app_engine, w, req):
    h = await user_auth(app_engine, w.org, w.owner_user)
    return await web.client.get(f"{DR}/{req['id']}/receipt", headers=h)


async def test_receipt_only_for_approved_404_otherwise(
    web, app_engine, owner_engine, worker_engine
):
    w, req, manager = await human_approved(owner_engine, app_engine)
    r = await receipt_of(web, app_engine, w, req)
    assert r.status_code == 200, r.text
    got = r.json()
    assert set(got) == {"alg", "kid", "payload", "signature"}
    assert got["payload"]["approvers"] == [str(manager)]
    aw, areq = await auto_approved(owner_engine, app_engine)
    auto = await receipt_of(web, app_engine, aw, areq)
    assert auto.status_code == 200 and auto.json()["payload"]["approvers"] == "auto"

    # catches: a receipt for a request that was never approved (FM15)
    pw = await make_world(owner_engine)
    pending = await open_request(app_engine, pw)
    cancelled = await open_request(app_engine, pw)
    await withdraw(app_engine, pw, cancelled, session=pw.session)
    rw, rejected, _ = await approval_world(owner_engine, app_engine, levels=ONE_LEVEL)
    step, *_ = await pending_step(owner_engine, rejected)
    await decide(app_engine, rw, step, await staff(owner_engine, rw.org, "manager"), approve=False)
    xw, expired, _ = await approval_world(owner_engine, app_engine, levels=ONE_LEVEL)
    awaiting = await open_request(app_engine, xw)  # left in pending_votes
    await timeouts(app_engine, worker_engine, NOW.now() + timedelta(minutes=31))
    cases = [
        (pw, pending, "pending_votes"),
        (pw, cancelled, "cancelled"),
        (rw, rejected, "rejected"),
        (xw, expired, "expired"),
        (xw, awaiting, None),
    ]
    for world, one, status in cases:
        if status:
            rows = await fetch(
                owner_engine, "select status from decision_requests where id=:r", r=one["id"]
            )
            assert rows == [(status,)]
        r = await receipt_of(web, app_engine, world, one)
        assert r.status_code == 404 and r.json()["error"]["code"] == "not_found", (status, r.text)


async def test_verify_is_public_but_reveals_only_valid_true_or_false(web, app_engine, owner_engine):
    w, req, _ = await human_approved(owner_engine, app_engine)
    receipt = (await receipt_of(web, app_engine, w, req)).json()
    # no credential at all: a third party checks a receipt it was handed
    r = await web.client.post(VERIFY, json=receipt)
    assert (r.status_code, r.json()) == (200, {"valid": True})
    assert "Refund" not in r.text and str(w.org) not in r.text
    forged = receipt | {"signature": "0" * 64}
    other = receipt | {"payload": receipt["payload"] | {"approvers": "auto"}}
    unknown = receipt | {"payload": receipt["payload"] | {"request_id": str(w.org)}}
    pending = await open_request(app_engine, w)
    replanted = receipt | {"payload": receipt["payload"] | {"request_id": str(pending["id"])}}
    # catches: verify leaking request content, or answering anything but a boolean
    for bad in (forged, other, unknown, replanted, {}, {"payload": "x"}, {"alg": None}):
        r = await web.client.post(VERIFY, json=bad)
        assert (r.status_code, r.json()) == (200, {"valid": False}), bad
    assert (await web.client.post(VERIFY, json=[receipt])).status_code == 422


@pytest.mark.parametrize(
    "edit",
    [
        "update decision_requests set title = 'Refund order 8' where id = :r",
        "update decision_requests set payload = '{\"amount\": 500}' where id = :r",
        "update decision_requests set risk_level = 'medium' where id = :r",
        "update decision_requests set version = 2 where id = :r",
        "update decision_requests set decided_at = decided_at + interval '1 second' where id = :r",
        "update approval_steps set approver_user_id = :other where decision_request_id = :r",
    ],
)
async def test_verify_after_db_edit_of_title_is_false(web, app_engine, owner_engine, edit):
    w, req, _ = await human_approved(owner_engine, app_engine)
    receipt = (await receipt_of(web, app_engine, w, req)).json()
    assert (await web.client.post(VERIFY, json=receipt)).json() == {"valid": True}
    other = await staff(owner_engine, w.org, "org_admin")
    async with owner_engine.begin() as c:  # an edit behind the app's back: the guards are off
        for t in ("decision_requests", "approval_steps"):
            await c.execute(text(f"alter table {t} disable trigger user"))
        params = {"r": req["id"]} | ({"other": other} if ":other" in edit else {})
        await c.execute(text(edit), params)
        for t in ("decision_requests", "approval_steps"):
            await c.execute(text(f"alter table {t} enable trigger user"))
    # catches: a receipt checked against itself or the stored hash, not the live request (I12, SC19)
    assert (await web.client.post(VERIFY, json=receipt)).json() == {"valid": False}


async def test_verify_is_rate_limited_per_ip(web, app_engine, owner_engine, monkeypatch):
    monkeypatch.setattr(settings, "write_limit_per_principal", 2)
    codes = [(await web.client.post(VERIFY, json={})).status_code for _ in range(3)]
    assert codes == [200, 200, 429]  # catches: an unauthenticated endpoint anyone can hammer

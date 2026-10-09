"""FM1 / SC20: every per-id REST route, as each kind of tenant-B principal, with tenant A's ids."""

import re
import uuid

import pytest

from app.db.session import tenant_session
from app.main import app
from app.services.invitations import create_invitation
from app.services.tokens import create_pat
from app.services.webhooks import create_subscription
from app.worker.webhooks import fan_out
from tests.api.harness import agent_auth
from tests.api.test_webhooks import answered_204
from tests.integration.helpers import (
    LEVELS3,
    NOW,
    _exec,
    cast,
    fetch,
    make_team,
    make_world,
    open_request,
    pending_step,
    staff,
    voters,
)
from tests.integration.web_harness import ORIGIN
from tests.integration.webhook_harness import emit

from .harness import ROUTES, api_routes

pytestmark = pytest.mark.anyio

# A minimal valid body for each route that needs one, so a 422 can never stand in for the 404.
BODIES = {
    ("PATCH", "/api/v1/members/{user_id}"): {"role": "member"},
    ("POST", "/api/v1/decision-requests/{decision_request_id}/revise"): {"title": "x"},
    ("POST", "/api/v1/decision-requests/{decision_request_id}/votes"): {
        "request_version": 1,
        "verdict": "approve",
        "confidence": 0.9,
    },
    ("POST", "/api/v1/decision-requests/{decision_request_id}/messages"): {"body": "hi"},
    ("POST", "/api/v1/approval-steps/{step_id}/decide"): {
        "request_version": 1,
        "decision": "approve",
    },
    ("PATCH", "/api/v1/teams/{team_id}"): {"name": "x"},
    ("PATCH", "/api/v1/escalation-policies/{policy_id}"): {"quorum_size": 1},
    ("PATCH", "/api/v1/webhook-subscriptions/{subscription_id}"): {"active": False},
}
PARAMS = {
    "organization_id", "invitation_id", "user_id", "decision_request_id", "step_id", "agent_id",
    "team_id", "policy_id", "token_id", "subscription_id", "delivery_id",
}  # fmt: skip


def test_every_registered_route_is_real_and_fillable():
    # catches: a stale ROUTES row (the router's own 404 would pass the probe below vacuously)
    real = {(m, p) for p, r in api_routes(app) for m in (r.methods or ())}
    assert set(ROUTES) <= real, set(ROUTES) - real
    for _, path in ROUTES:
        assert set(re.findall(r"{(\w+)}", path)) <= PARAMS, path


async def _tenant_a(owner, app_engine):
    """One of everything tenant A owns that a route can name; returns (ids, receipt request, A headers)."""
    w = await make_world(owner, "pro", floors={"refund": "medium", "note": "low"}, levels=LEVELS3)
    team = await make_team(owner, w.org)
    await _exec(
        owner, "update memberships set team_id=:t where organization_id=:o and user_id=:u",
        t=team, o=w.org, u=w.owner_user,
    )  # fmt: skip
    escalated = await open_request(app_engine, w)
    approved = await open_request(app_engine, w, action_type="note")
    for s in await voters(owner, w, 3):
        await cast(app_engine, w, escalated, s)
        await cast(app_engine, w, approved, s)
    assert await fetch(
        owner, "select status from decision_requests where id=:r", r=approved["id"]
    ) == [("approved",)]
    step, *_ = await pending_step(owner, escalated)
    admin = await staff(owner, w.org, "org_admin")
    async with tenant_session(app_engine, w.org) as s:
        pat, raw = await create_pat(s, w.org, admin, "a")
        invite, _ = await create_invitation(s, w.org, admin, "z@example.test", "member", None, NOW)
        hook, _ = await create_subscription(
            s, w.org, admin, "https://93.184.215.14/hook", ["decision.created"]
        )
    await fan_out(app_engine, w.org, await emit(app_engine, w.org), NOW, send=answered_204)
    [(delivery,)] = await fetch(
        owner, "select id from webhook_deliveries where subscription_id=:s", s=hook["id"]
    )
    ids = {
        "organization_id": w.org, "invitation_id": invite, "user_id": w.owner_user,
        "decision_request_id": escalated["id"], "step_id": step, "agent_id": w.identity,
        "team_id": team, "policy_id": w.policy, "token_id": pat, "subscription_id": hook["id"],
        "delivery_id": delivery,
    }  # fmt: skip
    agent = await agent_auth(app_engine, w.org, w.identity)
    return ids, approved["id"], {"Authorization": f"Bearer {raw}"}, agent


async def _tenant_b_principals(web, owner, app_engine):
    b = await make_world(owner)
    admin = await staff(owner, b.org, "org_admin")
    async with tenant_session(app_engine, b.org) as s:
        _, raw = await create_pat(s, b.org, admin, "b")
    email = f"b-{uuid.uuid4()}@example.test"
    await web.login(email)
    user = (await fetch(owner, "select id from users where email=:e", e=email))[0][0]
    await _exec(
        owner, "insert into memberships (organization_id, user_id, role) values (:o, :u, 'org_admin')",
        o=b.org, u=user,
    )  # fmt: skip
    r = await web.post("/api/v1/auth/organization", json={"organization_id": str(b.org)})
    assert r.status_code == 200, r.text
    return {
        "agent token": await agent_auth(app_engine, b.org, b.identity),
        "personal access token": {"Authorization": f"Bearer {raw}"},
        "session": {},  # the cookie set by the login above
    }


@pytest.mark.parametrize("route", sorted(ROUTES), ids=lambda r: f"{r[0]} {r[1]}")
async def test_tenant_b_gets_404_with_tenant_a_ids(route, web, app_engine, owner_engine):
    method, template = route
    ids, receipt_request, a_admin, a_agent = await _tenant_a(owner_engine, app_engine)
    if template.endswith("/receipt"):
        ids["decision_request_id"] = receipt_request  # only an approved request has a receipt
    path = template.format(**ids)
    nowhere = template.format(**{k: uuid.uuid4() for k in PARAMS})
    body = BODIES.get(route)
    twin = ("GET", template) in ROUTES or template.endswith("/receipt")
    before = (await web.client.get(path, headers=a_admin)).json() if twin else None
    if twin:  # the ids are real: tenant A itself reads them
        assert (await web.client.get(path, headers=a_admin)).status_code == 200
    found = []
    for who, h in (await _tenant_b_principals(web, owner_engine, app_engine)).items():
        h = {"Origin": ORIGIN} | h
        foreign = await web.client.request(method, path, json=body, headers=h)
        missing = await web.client.request(method, nowhere, json=body, headers=h)
        # catches: any answer (403, 409, 422, 200) that tells B tenant A's id exists
        assert (foreign.status_code, foreign.json()["error"]["code"]) == (
            missing.status_code,
            missing.json()["error"]["code"],
        ), (who, foreign.text, missing.text)
        assert foreign.status_code in (403, 404), (who, foreign.text)  # 403 = role, before any id
        if foreign.status_code == 404:
            found.append(who)
    assert found, "no tenant-B principal reached the id lookup: the probe proves nothing"
    # and nothing of A's changed: its resource reads the same, its agent token still works
    if twin:
        assert (await web.client.get(path, headers=a_admin)).json() == before
    assert (await web.client.get("/api/v1/me", headers=a_agent)).status_code == 200

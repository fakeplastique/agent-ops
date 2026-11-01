import uuid

import pytest

from tests.api.harness import user_auth
from tests.integration.helpers import approval_world, pending_step, staff

pytestmark = pytest.mark.anyio


async def test_steps_of_a_request_with_the_levels_and_quorum(web, app_engine, owner_engine):
    w, req, _ = await approval_world(owner_engine, app_engine, high=True)
    step, *_ = await pending_step(owner_engine, req)
    manager = await staff(owner_engine, w.org, "manager")
    h = await user_auth(app_engine, w.org, manager)
    r = await web.client.get(f"/api/v1/decision-requests/{req['id']}/steps", headers=h)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["total"] == 1 and body["quorum"] >= 1
    assert [lv["level"] for lv in body["levels"]] == list(range(1, len(body["levels"]) + 1))
    assert set(body["levels"][0]) == {"level", "role", "timeout_minutes"}
    s = body["items"][0]
    assert s["id"] == str(step) and s["status"] == "pending" and s["request_version"] == 1
    assert s["decided_at"] is None and s["decided_by"] is None and "due_at" in s
    # catches: the policy snapshot (other fields) or a request's content leaking through this read
    assert set(s) == {
        "id", "request_version", "level", "required_role", "status", "due_at", "decided_at",
        "decided_by", "stands_in_for_level", "created_at",
    }  # fmt: skip


async def test_a_decided_step_names_who_decided_it(web, app_engine, owner_engine):
    w, req, _ = await approval_world(owner_engine, app_engine, high=True)
    step, *_ = await pending_step(owner_engine, req)
    manager = await staff(owner_engine, w.org, "manager")
    h = await user_auth(app_engine, w.org, manager)
    done = await web.client.post(
        f"/api/v1/approval-steps/{step}/decide",
        json={"request_version": 1, "decision": "approve"},
        headers=h,
    )
    assert done.status_code == 200, done.text
    r = await web.client.get(f"/api/v1/decision-requests/{req['id']}/steps", headers=h)
    first = r.json()["items"][0]
    assert (
        first["status"] == "approved" and first["decided_by"] == "U"
    )  # the planted user's full name
    assert first["decided_at"] is not None


async def test_unknown_request_is_404(web, app_engine, owner_engine):
    w, _, _ = await approval_world(owner_engine, app_engine)
    h = await user_auth(app_engine, w.org, await staff(owner_engine, w.org, "member"))
    r = await web.client.get(f"/api/v1/decision-requests/{uuid.uuid4()}/steps", headers=h)
    assert r.status_code == 404

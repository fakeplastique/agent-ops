from urllib.parse import urlsplit

import pytest

from tests.api.harness import BODY, DR, agent_auth, user_auth
from tests.integration.helpers import add_agent, make_world, open_request, withdraw

pytestmark = pytest.mark.anyio
VOTE = {"request_version": 1, "verdict": "reject", "confidence": 0.7}


async def test_blind_votes_not_in_get_or_list_before_reveal(web, app_engine, owner_engine):
    w = await make_world(owner_engine, quorum=3)
    user = await user_auth(app_engine, w.org, w.owner_user)
    req = (await web.client.post(DR, json=BODY, headers=user)).json()
    voters = [
        await agent_auth(app_engine, w.org, (await add_agent(owner_engine, w))[0]) for _ in range(3)
    ]
    for i, h in enumerate(voters[:2]):
        body = VOTE | {"rationale": f"secret-rationale-{i}"}
        assert (
            await web.client.post(f"{DR}/{req['id']}/votes", json=body, headers=h)
        ).status_code == 201

    votes = f"{DR}/{req['id']}/votes"
    # catches: blind data via the list endpoint (and the request read, and another voter)
    for path, h in [(votes, user), (votes, voters[2]), (f"{DR}/{req['id']}", user), (DR, user)]:
        r = await web.client.get(path, headers=h)
        assert r.status_code == 200
        assert "secret-rationale" not in r.text and "reject" not in r.text
    seen = (await web.client.get(votes, headers=user)).json()
    assert seen["items"] == [] and seen["cast"] == 2 and seen["quorum"] == 3
    own = (await web.client.get(votes, headers=voters[0])).json()
    assert [v["rationale"] for v in own["items"]] == ["secret-rationale-0"]

    third = await web.client.post(votes, json=VOTE, headers=voters[2])
    assert third.status_code == 201 and third.json()["status"] != "pending_votes"
    revealed = (await web.client.get(votes, headers=user)).json()
    assert len(revealed["items"]) == 3


async def test_filters_status_risk_agent_q_combine_and_page(web, app_engine, owner_engine):
    w = await make_world(owner_engine)
    other_ident, other_sess = await add_agent(owner_engine, w)
    mine = []
    for i in range(3):
        mine.append(await open_request(app_engine, w, title=f"Refund {i}", risk_level="high"))
    await open_request(app_engine, w, title="Refund medium", risk_level="medium")
    await open_request(app_engine, w, title="Other high", risk_level="high")
    await open_request(
        app_engine, w, agent_session_id=other_sess, title="Refund theirs", risk_level="high"
    )
    await withdraw(app_engine, w, mine[2], session=w.session)
    h = await user_auth(app_engine, w.org, w.owner_user)

    q = {
        "status": "pending_votes",
        "risk_level": "high",
        "agent_id": str(w.identity),
        "q": "refund",
    }
    first = (await web.client.get(DR, headers=h, params=q | {"limit": 1, "sort": "title"})).json()
    # catches: a filter dropped when sorting or paging
    assert first["total"] == 2 and [d["title"] for d in first["items"]] == ["Refund 0"]
    nxt = urlsplit(first["nextLink"])
    second = (await web.client.get(f"{nxt.path}?{nxt.query}", headers=h)).json()
    assert [d["title"] for d in second["items"]] == ["Refund 1"] and second["nextLink"] is None

    theirs = (await web.client.get(DR, headers=h, params={"agent_id": str(other_ident)})).json()
    assert [d["title"] for d in theirs["items"]] == ["Refund theirs"]
    cancelled = (await web.client.get(DR, headers=h, params={"status": "cancelled"})).json()
    assert [d["id"] for d in cancelled["items"]] == [str(mine[2]["id"])]
    # LIKE metacharacters are literal
    assert (await web.client.get(DR, headers=h, params={"q": "%"})).json()["total"] == 0
    for sort in ("created_at", "-created_at", "updated_at", "-updated_at", "title", "-title"):
        r = await web.client.get(DR, headers=h, params={"sort": sort})
        assert r.status_code == 200 and r.json()["total"] == 6
    bad_params = [
        {"sort": "id; drop table x"},
        {"status": "nope"},
        {"risk_level": "LOW"},
        {"q": "a\x00b"},  # Postgres text cannot hold NUL: 422, not a 500
        {"skip": 10**20},
    ]
    for bad in bad_params:
        assert (await web.client.get(DR, headers=h, params=bad)).status_code == 422

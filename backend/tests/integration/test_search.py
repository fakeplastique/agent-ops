"""S33 / FM4 / I1 / I5: GET /search and the MCP search_decisions tool (Enterprise, `search` flag)."""

import uuid

import pytest
from sqlalchemy import text

from app.adapters import search_pg
from app.db.session import tenant_session
from tests.api.harness import agent_auth, user_auth
from tests.integration.helpers import (
    LEVELS3,
    approval_world,
    cast,
    fetch,
    make_world,
    open_request,
    staff,
    voters,
)
from tests.mcp.conftest import mcp  # noqa: F401 (the fixture)

pytestmark = pytest.mark.anyio

SEARCH = "/api/v1/search"
DR = "/api/v1/decision-requests"


async def ent(owner, app_engine, plan="enterprise", **policy):
    w = await make_world(owner, plan, **policy)
    return w, await agent_auth(app_engine, w.org, w.identity)


async def find(web, h, q, **params):
    return await web.client.get(SEARCH, params={"q": q, **params}, headers=h)


def ids(r) -> list[str]:
    assert r.status_code == 200, r.text
    return [i["decision_request_id"] for i in r.json()["items"]]


async def test_tenant_b_query_never_returns_tenant_a_hits_even_for_exact_titles(
    web, owner_engine, app_engine
):
    # catches: an index (or a query) that is not behind the tenant wall
    a, a_h = await ent(owner_engine, app_engine)
    b, b_h = await ent(owner_engine, app_engine)
    title = f"Zebra payout {uuid.uuid4().hex[:8]}"
    req = await open_request(app_engine, a, title=title, description="quarterly zebra run")
    assert ids(await find(web, a_h, title)) == [str(req["id"])]
    for q in (title, "zebra", f'"{title}"', title.upper()):
        r = await find(web, b_h, q)
        assert r.status_code == 200 and r.json()["items"] == [] and r.json()["total"] == 0, r.text
    # under B's tenant session even a query with no organization predicate cannot see A's rows (RLS)
    async with tenant_session(app_engine, b.org) as s:
        n = await s.scalar(
            text(
                "select count(*) from decision_requests where search_vector @@ plainto_tsquery('simple', 'zebra')"
            )
        )
    assert n == 0
    # the adapter asked for A by B's principal still scopes to the session's own organization
    page = await search_pg.PgSearch(app_engine).query(b.org, "zebra", 0, 20)
    assert page.items == [] and page.total == 0


async def test_every_principal_of_the_organization_reads_the_same_hits(
    web, owner_engine, app_engine
):
    w, agent = await ent(owner_engine, app_engine)
    req = await open_request(app_engine, w, title="Giraffe migration plan")
    member = await user_auth(app_engine, w.org, await staff(owner_engine, w.org, "member"))
    assert (
        ids(await find(web, agent, "giraffe"))
        == ids(await find(web, member, "giraffe"))
        == [str(req["id"])]
    )
    assert (await find(web, {}, "giraffe")).status_code == 401


async def test_round_one_vote_text_not_indexed(web, owner_engine, app_engine):
    # catches: a rationale (or verdict) findable before the reveal, or ever
    w, h = await ent(owner_engine, app_engine, floors={"refund": "medium"}, levels=LEVELS3)
    req = await open_request(app_engine, w, title="Refund order 9")
    a, b, c = await voters(owner_engine, w, 3)
    for s in (a, b):  # round 1 still blind: two of three cast
        await cast(app_engine, w, req, s, rationale="quokkarationale secretverdict")
    assert (
        await fetch(owner_engine, "select status from decision_requests where id=:r", r=req["id"])
    )[0][0] == "pending_votes"
    for q in ("quokkarationale", "secretverdict", "quokkarationale secretverdict"):
        assert ids(await find(web, h, q)) == [], q
    await cast(app_engine, w, req, c, rationale="quokkarationale secretverdict")  # reveal
    assert (
        await fetch(owner_engine, "select status from decision_requests where id=:r", r=req["id"])
    )[0][0] != "pending_votes"
    assert (
        ids(await find(web, h, "quokkarationale")) == []
    )  # votes are never indexed, revealed or not
    assert ids(await find(web, h, "refund")) == [str(req["id"])]  # the request itself is


async def test_free_and_pro_402_on_rest_and_mcp(web, mcp, owner_engine, app_engine):  # noqa: F811
    # catches: an ungated path
    for plan in ("free", "pro"):
        _w, h = await ent(owner_engine, app_engine, plan)
        r = await find(web, h, "x")
        assert r.status_code == 402, (plan, r.text)
        assert r.json()["error"]["details"] == {"flag": "search", "required_plan": "enterprise"}
        async with mcp.client(h) as c:
            t = await c.call_tool("search_decisions", {"q": "x"})
            assert t.is_error and "plan feature required" in t.content[0].text, (plan, t.content)


@pytest.mark.parametrize(
    "q",
    ["&", "|", "!", "()", ":*", "a:*", "<->", "a & b | !c", "'", "'''", '"', '"unbalanced', "a OR", "OR", "-",
     "-a", "\\", "%", "_", "*", "a\\", "'); drop table decision_requests;--", "\U0001f600", "\u202e", "\t\n",
     "word " * 40, "a" * 200],
)  # fmt: skip
async def test_query_syntax_characters_and_very_long_q_are_clean_4xx_or_literal(
    q, web, owner_engine, app_engine
):
    # catches: tsquery syntax injection or a 500 from the parser
    w, h = await ent(owner_engine, app_engine)
    await open_request(app_engine, w, title="Plain title with 100% literal_chars")
    r = await find(web, h, q)
    assert r.status_code in (200, 422), (q, r.status_code, r.text)
    if r.status_code == 200 and not q.strip().strip("&|!()':*-<>\\\"%_"):
        assert r.json()["items"] == [], q  # punctuation is not a wildcard: it matches nothing


@pytest.mark.parametrize("q", ["", "   ", "a\x00b", "x" * 201])
async def test_blank_nul_and_overlong_q_are_422(q, web, owner_engine, app_engine):
    _, h = await ent(owner_engine, app_engine)
    r = await find(web, h, q)
    assert r.status_code == 422 and r.json()["error"]["code"] == "invalid_input", r.text


async def test_a_lone_surrogate_in_q_is_422_not_500(web, owner_engine, app_engine):
    _, h = await ent(owner_engine, app_engine)
    r = await web.client.get(
        f"{SEARCH}?q=%ED%A0%80", headers=h
    )  # an encoded surrogate: not valid UTF-8
    assert r.status_code in (200, 422), r.text


async def test_missing_q_and_bad_paging_are_422(web, owner_engine, app_engine):
    _, h = await ent(owner_engine, app_engine)
    assert (await web.client.get(SEARCH, headers=h)).status_code == 422
    for p in ({"limit": 0}, {"limit": 101}, {"skip": -1}, {"skip": 1001}, {"limit": "x"}):
        assert (await find(web, h, "a", **p)).status_code == 422, p


async def test_index_updates_on_revise_and_message(web, owner_engine, app_engine):
    # catches: stale hits after a revise, or a message that cannot be found
    w, req, _ = await approval_world(owner_engine, app_engine, plan="enterprise")
    h = await agent_auth(app_engine, w.org, w.identity)
    assert ids(await find(web, h, "refund")) == [str(req["id"])]
    assert ids(await find(web, h, "okapi")) == []
    posted = await web.client.post(
        f"{DR}/{req['id']}/messages", json={"body": "the okapi shipment is late"}, headers=h
    )
    assert posted.status_code == 201, posted.text
    r = await find(web, h, "okapi")
    assert ids(r) == [str(req["id"])] and r.json()["items"][0]["matched_in"] == ["message"]
    # a revise rewrites the title: the old words stop matching, the new ones match
    rev = await web.client.post(
        f"{DR}/{req['id']}/revise", json={"title": "Vicuna transfer"}, headers=h
    )
    assert rev.status_code == 200, rev.text
    # the old title is gone from the index; only the revise note in the thread still names it
    old = await find(web, h, "refund")
    assert [i["matched_in"] for i in old.json()["items"]] == [["message"]], old.text
    r = await find(web, h, "vicuna")
    assert ids(r) == [str(req["id"])] and "title" in r.json()["items"][0]["matched_in"]
    assert ids(await find(web, h, "okapi")) == [str(req["id"])]  # the thread survives a revise


async def test_one_hit_per_request_with_every_place_it_matched(web, owner_engine, app_engine):
    w, req, _ = await approval_world(owner_engine, app_engine, plan="enterprise")
    h = await agent_auth(app_engine, w.org, w.identity)
    for body in ("tapir one", "tapir two"):
        assert (
            await web.client.post(f"{DR}/{req['id']}/messages", json={"body": body}, headers=h)
        ).status_code == 201
    other = await open_request(app_engine, w, title="Tapir census", description="count the tapir")
    r = await find(web, h, "tapir")
    got = {i["decision_request_id"]: i for i in r.json()["items"]}
    assert r.json()["total"] == 2 and set(got) == {str(req["id"]), str(other["id"])}
    assert got[str(req["id"])]["matched_in"] == ["message"]
    assert got[str(other["id"])]["matched_in"] == ["title", "description"]
    assert set(got[str(other["id"])]) == {
        "decision_request_id", "title", "status", "effective_risk_level", "created_at", "matched_in", "rank",
    }  # fmt: skip


async def test_results_are_paged_ranked_and_bounded(web, owner_engine, app_engine, monkeypatch):
    # catches: an unbounded scan or a total that counts more than the window it can page
    monkeypatch.setattr(search_pg, "CANDIDATES", 5)
    w, h = await ent(owner_engine, app_engine)
    for i in range(8):
        await open_request(app_engine, w, title=f"Narwhal batch {i}")
    first = await find(web, h, "narwhal", limit=3)
    body = first.json()
    assert body["total"] == 5 and len(body["items"]) == 3 and body["nextLink"], body
    assert "skip=3" in body["nextLink"] and "q=narwhal" in body["nextLink"]
    second = await web.client.get(body["nextLink"].removeprefix("http://test"), headers=h)
    assert len(second.json()["items"]) == 2 and second.json()["nextLink"] is None
    seen = ids(first) + ids(second)
    assert len(set(seen)) == 5  # pages never overlap
    best = await find(web, h, "narwhal batch 3")
    assert best.json()["items"][0]["title"] == "Narwhal batch 3"  # the better match ranks first


# MCP


async def test_mcp_search_decisions_pages_and_names_the_next_call(mcp, owner_engine, app_engine):  # noqa: F811
    w, h = await ent(owner_engine, app_engine)
    for i in range(3):
        await open_request(app_engine, w, title=f"Axolotl {i}")
    other, _ = await ent(owner_engine, app_engine)
    await open_request(app_engine, other, title="Axolotl foreign")
    async with mcp.client(h) as c:
        r = await c.call_tool("search_decisions", {"q": "axolotl", "limit": 2})
        assert not r.is_error, r.content
        body = r.structured_content
        assert body["total"] == 3 and len(body["items"]) == 2
        assert body["nextLink"] == "search_decisions?q=axolotl&skip=2&limit=2"
        assert all("foreign" not in i["title"] for i in body["items"])
        for bad in (
            {"q": ""},
            {"q": "x" * 201},
            {"q": "a\x00"},
            {"q": "a", "limit": 101},
            {"q": "a", "skip": 1001},
        ):
            t = await c.call_tool("search_decisions", bad)
            assert t.is_error, bad

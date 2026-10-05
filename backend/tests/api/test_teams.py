import asyncio

import pytest

from app.db.session import tenant_session
from app.domain.errors import InvalidInput
from app.services import teams
from tests.api.harness import agent_auth, user_auth
from tests.integration.helpers import fetch, make_team, make_world, staff

pytestmark = pytest.mark.anyio

TM = "/api/v1/teams"


async def _admin(app_engine, owner_engine):
    w = await make_world(owner_engine)
    return w, await user_auth(app_engine, w.org, await staff(owner_engine, w.org, "org_admin"))


async def test_team_tree_crud(web, app_engine, owner_engine):
    w, admin = await _admin(app_engine, owner_engine)
    root = await web.client.post(TM, json={"name": "Ops"}, headers=admin)
    assert root.status_code == 201 and root.headers["location"] == f"{TM}/{root.json()['id']}"
    child = await web.client.post(
        TM, json={"name": "Payments", "parent_team_id": root.json()["id"]}, headers=admin
    )
    assert child.status_code == 201 and child.json()["parent_team_id"] == root.json()["id"]
    one = f"{TM}/{child.json()['id']}"
    assert (await web.client.post(TM, json={"name": "Ops"}, headers=admin)).status_code == 409
    moved = await web.client.patch(one, json={"parent_team_id": None}, headers=admin)
    assert moved.status_code == 200 and moved.json()["parent_team_id"] is None
    renamed = await web.client.patch(one, json={"name": "Pay"}, headers=admin)
    assert renamed.json()["name"] == "Pay" and renamed.json()["parent_team_id"] is None
    listed = await web.client.get(TM, headers=await agent_auth(app_engine, w.org, w.identity))
    assert listed.status_code == 200 and listed.json()["total"] == 2
    for bad in ({"name": ""}, {"name": "x" * 81}, {"name": "a", "colour": "red"}, {"name": None}):
        assert (await web.client.post(TM, json=bad, headers=admin)).status_code == 422, bad
    # a team with a sub-team (or members, a policy) cannot vanish under them
    await web.client.patch(one, json={"parent_team_id": root.json()["id"]}, headers=admin)
    assert (await web.client.delete(f"{TM}/{root.json()['id']}", headers=admin)).status_code == 409
    assert (await web.client.delete(one, headers=admin)).status_code == 204
    assert (await web.client.get(one, headers=admin)).status_code == 404


async def test_non_admin_cannot_write_teams(web, app_engine, owner_engine):
    w, _ = await _admin(app_engine, owner_engine)
    team = await make_team(owner_engine, w.org)
    for h in (
        await agent_auth(app_engine, w.org, w.identity),
        await user_auth(app_engine, w.org, await staff(owner_engine, w.org, "manager")),
    ):
        assert (await web.client.post(TM, json={"name": "n"}, headers=h)).status_code == 403
        r = await web.client.patch(f"{TM}/{team}", json={"name": "n"}, headers=h)
        assert r.status_code == 403
        assert (await web.client.delete(f"{TM}/{team}", headers=h)).status_code == 403


async def test_parent_cycle_rejected_including_via_concurrent_moves(web, app_engine, owner_engine):
    w, admin = await _admin(app_engine, owner_engine)
    a = await make_team(owner_engine, w.org)
    b = await make_team(owner_engine, w.org, parent=a)
    c = await make_team(owner_engine, w.org, parent=b)
    for team, parent in ((a, a), (a, c), (a, b)):
        r = await web.client.patch(f"{TM}/{team}", json={"parent_team_id": str(parent)}, headers=admin)
        assert r.status_code == 422, r.text  # catches: a loop that hangs policy resolution
    x, y = await make_team(owner_engine, w.org), await make_team(owner_engine, w.org)
    # Two moves that are each fine alone, together a loop: x under y while y goes under x.
    async with tenant_session(app_engine, w.org) as s1:
        await teams.update_team(s1, w.org, w.owner_user, x, {"parent_team_id": y})
        second = asyncio.create_task(_move(app_engine, w, y, x))
        await asyncio.sleep(0.5)
        # catches: the second move checking a snapshot without the first (no serialisation)
        assert not second.done(), "the second move did not wait for the first"
    with pytest.raises(InvalidInput):
        await second
    rows = await fetch(
        owner_engine, "select id, parent_team_id from teams where id in (:x, :y)", x=x, y=y
    )
    assert dict(rows) == {x: y, y: None}


async def _move(app_engine, w, team, parent):
    async with tenant_session(app_engine, w.org) as s:
        await teams.update_team(s, w.org, w.owner_user, team, {"parent_team_id": parent})


async def test_team_in_other_org_as_parent_404(web, app_engine, owner_engine):
    w, admin = await _admin(app_engine, owner_engine)
    other = await make_world(owner_engine)
    foreign = await make_team(owner_engine, other.org)
    mine = await make_team(owner_engine, w.org)
    # catches: a cross-tenant parent (the composite FK would 500, a 409/422 would confirm it exists)
    r = await web.client.post(TM, json={"name": "n", "parent_team_id": str(foreign)}, headers=admin)
    assert r.status_code == 404, r.text
    r = await web.client.patch(f"{TM}/{mine}", json={"parent_team_id": str(foreign)}, headers=admin)
    assert r.status_code == 404, r.text
    assert await fetch(owner_engine, "select parent_team_id from teams where id=:t", t=mine) == [
        (None,)
    ]

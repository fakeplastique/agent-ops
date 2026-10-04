import pytest
from sqlalchemy import text

from app.db.session import tenant_session
from app.services import outbox

from .helpers import fetch, make_org

pytestmark = pytest.mark.anyio


async def test_seq_is_untouched_by_publishing(app_engine, owner_engine):
    org = await make_org(owner_engine)
    async with tenant_session(app_engine, org) as s:
        for i in range(3):
            await outbox.emit(s, org, "decision.created", {"i": i})
    before = await fetch(
        owner_engine, "select seq from outbox_events where organization_id=:o order by seq", o=org
    )
    async with (
        owner_engine.begin() as c
    ):  # what the relay does to a row: published_at, nothing else
        await c.execute(
            text("update outbox_events set published_at = now() where organization_id=:o"),
            {"o": org},
        )
    after = await fetch(
        owner_engine, "select seq from outbox_events where organization_id=:o order by seq", o=org
    )
    assert before == after == [(1,), (2,), (3,)]

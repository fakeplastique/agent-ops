"""Plant fresh tenants for the newman collection and print their credentials as `--env-var` flags.

Writes as the owner (MIGRATE_DATABASE_URL) through the same helpers the integration tests use; tokens are
minted by the real token service. Every run makes new organizations, so it is safe to rerun."""

import asyncio
import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # the backend dir: `app` and `tests`

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from app.config import settings
from app.db.engine import make_engine
from tests.api.harness import agent_auth, user_auth
from tests.integration.helpers import (
    make_agent,
    make_member,
    make_org,
    make_policy,
    make_user,
    set_usage,
    staff,
)


def _token(headers: dict[str, str]) -> str:
    return headers["Authorization"].removeprefix("Bearer ")


async def seed() -> dict[str, str]:
    owner = create_async_engine(settings.migrate_database_url, poolclass=NullPool)
    app = make_engine(settings.database_url)
    try:
        # Free org: the requester's owner is a plain member, so the org_admin may decide its steps.
        org = await make_org(owner, "free")
        agent_owner = await make_user(owner)
        await make_member(owner, org, agent_owner)
        admin = await staff(owner, org, "org_admin")
        await make_policy(
            owner, org, quorum=3, floors={"refund": "medium", "note": "low"},
            levels=[{"role": "org_admin", "timeout_minutes": 60}],
        )  # fmt: skip
        agents = [(await make_agent(owner, org, agent_owner))[0] for _ in range(4)]
        out = {
            "run": uuid.uuid4().hex[:12],
            "adminToken": _token(await user_auth(app, org, admin)),
            "requesterId": str(agents[0]),
            "voterId": str(agents[1]),
        }
        for i, a in enumerate(agents):
            out["requesterToken" if i == 0 else f"voter{i}Token"] = _token(
                await agent_auth(app, org, a)
            )
        # A second Free org that has used up this month's requests.
        full = await make_org(owner, "free")
        await make_member(owner, full, user := await make_user(owner))
        async with owner.connect() as c:
            limit = await c.scalar(text("select requests_per_month from plans where key = 'free'"))
        await set_usage(owner, full, datetime.now(UTC).strftime("%Y-%m"), limit)
        out["fullOrgToken"] = _token(
            await agent_auth(app, full, (await make_agent(owner, full, user))[0])
        )
        return out
    finally:
        await owner.dispose()
        await app.dispose()


if __name__ == "__main__":
    print(" ".join(f"--env-var {k}={v}" for k, v in asyncio.run(seed()).items()))

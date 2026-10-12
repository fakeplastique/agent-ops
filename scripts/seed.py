"""Seed the two demo organizations: `uv run --project backend python scripts/seed.py`.

Runs as the app role (DATABASE_URL) through the services, so it is safe to repeat: a re-run adds only what is
missing. Agent tokens and webhook secrets are printed once, on the run that creates them."""

import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.adapters.clock import SystemClock
from app.config import settings
from app.db.engine import make_engine
from app.db.guard import assert_safe_role
from app.services import demo


async def main() -> None:
    if os.environ.get("RAILWAY_ENVIRONMENT") and not settings.demo_mode:
        sys.exit("refusing to seed a hosted database unless DEMO_MODE=true")
    engine = make_engine(settings.database_url)
    try:
        await assert_safe_role(engine)  # the same guard as the API: never seed around RLS
        seeded = await demo.seed(engine, SystemClock())
        users = await demo.seeded_users(engine)
    finally:
        await engine.dispose()
    for o in demo.ORGS:
        print(f"{o.name} ({o.plan}), id {demo.org_id(o.slug)}")
    for u in users:
        print(f"  {u['role']:<10} {u['email']}  {u['name']}")
    for name, secret in seeded.secrets.items():
        print(f"secret {name}: {secret}")
    if not seeded.secrets:
        print("nothing new: the tokens and secrets printed by the first run are not shown again")


asyncio.run(main())

#!/usr/bin/env bash
# Create the app role, then migrate to head as the owner (MIGRATE_DATABASE_URL); one transaction, all or nothing.
set -euo pipefail
cd "$(dirname "$0")/.."
: "${MIGRATE_DATABASE_URL:?set MIGRATE_DATABASE_URL to the owner role}"
export BACKEND_DIR=${BACKEND_DIR:-backend}

uv run --project backend python - <<'PY'
import asyncio, os, sys
from pathlib import Path

sys.path.insert(0, os.environ["BACKEND_DIR"])  # lets skew_check migrate with an older tree
from sqlalchemy.ext.asyncio import create_async_engine

from app.db.migrate import upgrade

url = os.environ["MIGRATE_DATABASE_URL"].replace("postgresql://", "postgresql+asyncpg://", 1)
roles = Path("scripts/db_roles.sql").read_text()


async def main() -> None:
    engine = create_async_engine(url)
    async with engine.begin() as c:
        await c.exec_driver_sql(roles)
        await c.run_sync(upgrade)
    await engine.dispose()


asyncio.run(main())
PY

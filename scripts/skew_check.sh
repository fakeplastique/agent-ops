#!/usr/bin/env bash
# FM34: deploys survive version skew. Usage: skew_check.sh [previous-ref] (default HEAD^). Needs compose postgres+redis.
set -euo pipefail
cd "$(dirname "$0")/.."
PREV=${1:-HEAD^}
DB=skew_check PORT=8011
OWNER=postgresql://app:app@localhost:5432/$DB
APP=postgresql://app_rt:app_rt@localhost:5432/$DB

tmp=$(mktemp -d)
cleanup() { [ -z "${pid:-}" ] || kill "$pid" 2>/dev/null || true; git worktree remove --force "$tmp/prev" 2>/dev/null || true; rm -rf "$tmp"; }
trap cleanup EXIT

psql() { docker compose exec -T postgres psql -v ON_ERROR_STOP=1 -U app -qtA "$@"; }
migrate() { MIGRATE_DATABASE_URL=$OWNER BACKEND_DIR=$1 scripts/migrate.sh; }
# boot <backend dir>: serve that build against the app role and require /readyz 200.
boot() {
  DATABASE_URL=$APP uv run --project backend uvicorn app.main:app --app-dir "$1" --port $PORT --log-level warning &
  pid=$!
  curl -sf -o /dev/null --retry 30 --retry-delay 1 --retry-all-errors localhost:$PORT/readyz
  kill "$pid"; wait "$pid" 2>/dev/null || true; pid=
}
version() { psql -d $DB -c "select version_num from alembic_version"; }

git worktree add -q --detach "$tmp/prev" "$PREV"
psql -d postgres -c "drop database if exists $DB with (force)" -c "create database $DB"

echo "== old schema, new build"
migrate "$tmp/prev/backend"
boot backend
echo "== new additive schema, old build"
migrate backend
boot "$tmp/prev/backend"

echo "== migration that fails halfway leaves the schema untouched"
head=$(version)
cp -r backend "$tmp/broken"
cat > "$tmp/broken/app/db/migrations/versions/9999_half.py" <<PY
from alembic import op

revision = "9999"
down_revision = "$head"


def upgrade() -> None:
    op.execute("create table skew_half (id int)")
    raise RuntimeError("fails halfway")
PY
if migrate "$tmp/broken"; then echo "FAIL: broken migration succeeded"; exit 1; fi
[ "$(version)" = "$head" ] || { echo "FAIL: version moved to $(version)"; exit 1; }
[ -z "$(psql -d $DB -c "select to_regclass('skew_half')")" ] || { echo "FAIL: partial table left"; exit 1; }

echo "== APP_RT_PASSWORD replaces the dev password (restored afterwards: the role is cluster-wide)"
APP_RT_PASSWORD=probe-pw migrate backend
login() { PGPASSWORD=$1 command psql -h localhost -U app_rt -d $DB -tAc "select 1" 2>/dev/null || true; }
new=$(login probe-pw) old=$(login app_rt)
APP_RT_PASSWORD=app_rt migrate backend
[ "$new" = 1 ] && [ -z "$old" ] || { echo "FAIL: dev password still works or new one rejected"; exit 1; }
echo "SKEW OK"

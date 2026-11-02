#!/usr/bin/env bash
# Full gate (what CI runs). Runs every step, prints a summary, exits non-zero if any failed.
set -uo pipefail
cd "$(dirname "$0")/.."

failed=()

# GATE_LANE=<n> lets several worktrees gate at once: own ports, databases, Redis db and image stack.
# Unset, the gate behaves as before (one shared dev DB, :8000, :3100).
LANE=${GATE_LANE:-}
if [ -n "$LANE" ]; then
  export COMPOSE_PROJECT_NAME=${COMPOSE_PROJECT_NAME:-webtech} # shared postgres/redis, whatever the worktree is called
  export GATE_API_PORT=${GATE_API_PORT:-$((8100 + LANE))} GATE_WEB_PORT=${GATE_WEB_PORT:-$((3200 + LANE))}
  export GATE_DEV_DB=${GATE_DEV_DB:-agent_ops_gate_$LANE} SKEW_DB=skew_check_$LANE SKEW_PORT=$((8300 + LANE))
  export TEST_DB_NAME=${TEST_DB_NAME:-agent_ops_test_gate_$LANE}
  export TEST_REDIS_URL=${TEST_REDIS_URL:-redis://localhost:6379/$((LANE % 16))} REDIS_URL=${TEST_REDIS_URL:-redis://localhost:6379/$((LANE % 16))}
  export SKEW_SKIP_ROLE_PASSWORD=1 # the app_rt password is cluster-wide: probing it would break other lanes
fi
API_PORT=${GATE_API_PORT:-8000} WEB_PORT=${GATE_WEB_PORT:-3100} DEV_DB=${GATE_DEV_DB:-agent_ops}
export API_PORT WEB_PORT
if [ -n "$LANE" ]; then
  export DATABASE_URL=postgresql://app_rt:app_rt@localhost:5432/$DEV_DB
  export MIGRATE_DATABASE_URL=postgresql://app:app@localhost:5432/$DEV_DB
  export WORKER_DATABASE_URL=postgresql://app_worker:app_worker@localhost:5432/$DEV_DB
fi

# Production images build, run as non-root and pass their healthchecks (compose prod profile).
images() {
  local rc=0 prod="docker compose --profile prod"
  # A lane builds and runs its own stack (random host ports) so lanes never share containers.
  [ -z "$LANE" ] || { prod="docker compose -p webtech-gate-$LANE --profile prod"; export PG_PUBLISH=5432 REDIS_PUBLISH=6379; }
  $prod build || rc=1
  # Railway's pre-deploy command: the image itself must be able to migrate as the owner (it also creates
  # app_rt, which a lane's fresh postgres lacks, so it runs before the api starts).
  $prod up -d --wait postgres redis && $prod run --rm --no-deps -e MIGRATE_DATABASE_URL=postgresql://app:app@postgres:5432/agent_ops \
    api scripts/migrate.sh || { echo "migrate.sh fails inside the api image"; rc=1; }
  $prod up -d --wait api web || rc=1
  for s in api web; do
    [ "$($prod exec -T $s id -u 2>/dev/null)" != 0 ] || { echo "$s runs as root"; rc=1; }
  done
  ((rc == 0)) || $prod logs --tail 30
  $prod rm -sf api web >/dev/null
  [ -z "$LANE" ] || $prod down -v >/dev/null 2>&1
  return $rc
}

step() { # step <name> <cmd>...
  local name=$1; shift
  echo "::group::$name"
  if "$@"; then echo "PASS $name"; else echo "FAIL $name"; failed+=("$name"); fi
  echo "::endgroup::"
}

step "backend format" uv run --project backend ruff format --check backend
step "backend lint" uv run --project backend ruff check backend
step "backend typecheck" uv run --project backend pyright --project backend/pyproject.toml
step "services up" docker compose up -d --wait postgres redis
dev_db() {
  docker compose exec -T postgres psql -U app -d postgres -tAc "select 1 from pg_database where datname = '$DEV_DB'" | grep -q 1 \
    || docker compose exec -T postgres psql -U app -d postgres -qc "create database $DEV_DB"
  MIGRATE_DATABASE_URL=postgresql://app:app@localhost:5432/$DEV_DB scripts/migrate.sh
}
step "migrate dev db" dev_db
step "backend tests" uv run --project backend pytest backend/tests

step "images" images
step "version skew" "${SKEW_CHECK:-scripts/skew_check.sh}"
step "frontend install" pnpm --dir frontend install --frozen-lockfile
step "frontend lint" pnpm --dir frontend lint
# Generated route types from an earlier build name routes that may have moved: start from none, as a fresh clone does.
rm -rf frontend/.next/types frontend/.next/dev
step "frontend typecheck" pnpm --dir frontend typecheck
step "frontend tests" pnpm --dir frontend test

# One API instance shared by newman and Playwright (it reuses a server on $API_PORT).
# A server already answering there would be tested instead of our code: fail, never reuse.
if curl -sf localhost:$API_PORT/healthz >/dev/null; then
  echo "FAIL api port: something already answers on :$API_PORT"
  failed+=("api port")
else
  uv run --project backend uvicorn app.main:app --app-dir backend --port $API_PORT --log-level warning &
  api=$!
  trap 'kill $api 2>/dev/null' EXIT
fi
step "api up" curl -sf -o /dev/null --retry 30 --retry-delay 1 --retry-all-errors localhost:$API_PORT/healthz

# Fresh tenants every run; their tokens reach newman as --env-var flags.
collection() {
  local vars
  vars=$(uv run --project backend python backend/postman/seed.py) || return 1
  # shellcheck disable=SC2086 # one word per flag
  npx --yes newman run backend/postman/collection.json --env-var baseUrl=http://localhost:$API_PORT $vars
}
step "api collection" collection
step "frontend e2e" env API_ORIGIN=http://localhost:$API_PORT pnpm --dir frontend exec playwright test

if ((${#failed[@]})); then
  echo "GATE FAILED: ${failed[*]}"
  exit 1
fi
echo "GATE PASSED"

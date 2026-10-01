#!/usr/bin/env bash
# Full gate (what CI runs). Runs every step, prints a summary, exits non-zero if any failed.
set -uo pipefail
cd "$(dirname "$0")/.."

failed=()

# Production images build, run as non-root and pass their healthchecks (compose prod profile).
images() {
  local rc=0 prod="docker compose --profile prod"
  $prod build && $prod up -d --wait api web || rc=1
  for s in api web; do
    [ "$($prod exec -T $s id -u 2>/dev/null)" -gt 0 ] 2>/dev/null || { echo "$s is not running as non-root"; rc=1; }
  done
  ((rc == 0)) || $prod logs --tail 30
  $prod rm -sf api web >/dev/null
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
step "migrate dev db" env MIGRATE_DATABASE_URL=postgresql://app:app@localhost:5432/agent_ops scripts/migrate.sh
step "backend tests" uv run --project backend pytest backend/tests

step "images" images
step "frontend install" pnpm --dir frontend install --frozen-lockfile
step "frontend lint" pnpm --dir frontend lint
step "frontend typecheck" pnpm --dir frontend typecheck
step "frontend tests" pnpm --dir frontend test

# One API instance shared by newman and Playwright (it reuses a server on :8000).
# A server already answering there would be tested instead of our code: fail, never reuse.
if curl -sf localhost:8000/healthz >/dev/null; then
  echo "FAIL api port: something already answers on :8000"
  failed+=("api port")
else
  uv run --project backend uvicorn app.main:app --app-dir backend --port 8000 --log-level warning &
  api=$!
  trap 'kill $api 2>/dev/null' EXIT
fi
step "api up" curl -sf -o /dev/null --retry 30 --retry-delay 1 --retry-all-errors localhost:8000/healthz

step "api collection" npx --yes newman run backend/postman/collection.json
step "frontend e2e" pnpm --dir frontend exec playwright test

if ((${#failed[@]})); then
  echo "GATE FAILED: ${failed[*]}"
  exit 1
fi
echo "GATE PASSED"

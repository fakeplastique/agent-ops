#!/usr/bin/env bash
# Full gate (what CI runs). Runs every step, prints a summary, exits non-zero if any failed.
set -uo pipefail
cd "$(dirname "$0")/.."

failed=()
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
step "backend tests" uv run --project backend pytest backend/tests

step "frontend install" pnpm --dir frontend install --frozen-lockfile
step "frontend lint" pnpm --dir frontend lint
step "frontend typecheck" pnpm --dir frontend typecheck
step "frontend tests" pnpm --dir frontend test

# One API instance shared by newman and Playwright (it reuses a server on :8000).
uv run --project backend uvicorn app.main:app --app-dir backend --port 8000 --log-level warning &
api=$!
trap 'kill $api 2>/dev/null' EXIT
for _ in $(seq 60); do
  kill -0 $api 2>/dev/null || break
  curl -sf localhost:8000/healthz >/dev/null && break
  sleep 0.5
done

step "api collection" npx --yes newman run backend/postman/collection.json
step "frontend e2e" pnpm --dir frontend exec playwright test

if ((${#failed[@]})); then
  echo "GATE FAILED: ${failed[*]}"
  exit 1
fi
echo "GATE PASSED"

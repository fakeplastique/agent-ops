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

if ((${#failed[@]})); then
  echo "GATE FAILED: ${failed[*]}"
  exit 1
fi
echo "GATE PASSED"

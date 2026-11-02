#!/usr/bin/env bash
# Docs drift check: the docs must describe the repo that exists. Exits non-zero listing every mismatch.
#   - every route in OpenAPI (plus /healthz, /readyz) is in docs/API.md, and every route API.md names exists
#     (a route inside <!-- planned --> ... <!-- /planned --> may not exist yet)
#   - every MCP tool is in API.md; every realtime event type is in REALTIME.md; every Settings field is in the README
#   - the README's JSON blocks parse and its MCP config points at /mcp with a bearer header
#   - ARCHITECTURE.md's state diagram has exactly the transitions of domain/transitions.py
#   - every repo path named in the docs (and in .ai/, when present) exists
# DOCS_ROOT (default: the repo) is where README.md, docs/ and .ai/ are read; the code is always this repo's.
set -euo pipefail
REPO=$(cd "$(dirname "$0")/.." && pwd)
export REPO DOCS_ROOT=${DOCS_ROOT:-$REPO}
read -ra py <<< "${DOCS_PYTHON:-uv run --project $REPO/backend python}"
PYTHONPATH="$REPO/backend" "${py[@]}" - <<'PY'
import json
import os
import re
import sys
from pathlib import Path

repo, docs = Path(os.environ["REPO"]), Path(os.environ["DOCS_ROOT"])
problems: list[str] = []


def read(rel: str) -> str:
    p = docs / rel
    if not p.is_file():
        problems.append(f"{rel} is missing")
        return ""
    return p.read_text()


readme, api, realtime, arch = (read(f) for f in ("README.md", "docs/API.md", "docs/REALTIME.md", "docs/ARCHITECTURE.md"))

from app.main import app  # noqa: E402  (after PYTHONPATH; no database is touched)
from app.config import Settings  # noqa: E402
from app.domain.transitions import TABLE  # noqa: E402
from app.mcp.tools import read as mcp_read, write as mcp_write  # noqa: E402
from app.realtime.projection import TYPES  # noqa: E402

# 1. routes
code = {f"{m.upper()} {p}" for p, ops in app.openapi()["paths"].items() for m in ops} | {"GET /healthz", "GET /readyz"}
planned = "".join(re.findall(r"<!-- planned -->(.*?)<!-- /planned -->", api, re.S))
route = re.compile(r"`((?:GET|POST|PUT|PATCH|DELETE) /[^`\s]*)`")
named, named_planned = set(route.findall(api)), set(route.findall(planned))
problems += [f"API.md lacks route {r}" for r in sorted(code - named)]
problems += [f"API.md names route {r} that OpenAPI does not have (mark it planned?)" for r in sorted(named - named_planned - code)]
for r in sorted(named_planned & code):  # a warning: the merge that lands the route should also drop the banner
    print(f"warning: API.md marks {r} planned but it exists now")

# 2. MCP tools, event types, settings: both directions
tools = {f.__name__ for f in mcp_read.TOOLS} | {f.__name__ for f, _ in mcp_write.TOOLS}
settings = {n.upper() for n in Settings.model_fields}
# Variables the README documents that are not Settings fields (read by the web build and by scripts/migrate.sh).
extra_env = {"API_ORIGIN", "APP_RT_PASSWORD", "APP_WORKER_PASSWORD"}
for what, found in (("OpenAPI routes", code), ("MCP tools", tools), ("event types", TYPES), ("settings", settings), ("transitions", TABLE)):
    if not found:
        problems.append(f"the code side of the {what} check is empty: the check would pass vacuously")


def first_cells(text: str) -> set[str]:
    """Backticked names in the first column of the markdown table rows in `text`."""
    return {t for row in re.findall(r"^\| ([^|]*)\|", text, re.M) for t in re.findall(r"`([^`]+)`", row)}


tools_doc = api.split("## MCP tools", 1)[-1]
tools_doc = re.sub(r"<!-- planned -->.*?<!-- /planned -->", "", tools_doc, flags=re.S)
doc_tools = first_cells(tools_doc)
problems += [f"API.md lacks MCP tool {t}" for t in sorted(tools - doc_tools)]
problems += [f"API.md names MCP tool {t} that does not exist" for t in sorted(doc_tools - tools)]

para = re.search(r"Event types:(.*?)\n\n", realtime, re.S)
doc_events = set(re.findall(r"`([a-z_]+\.[a-z_]+)`", para.group(1).split("(")[0])) if para else set()
problems += [f"REALTIME.md lacks event type {t}" for t in sorted(set(TYPES) - doc_events)]
problems += [f"REALTIME.md names event type {t} that is not emitted" for t in sorted(doc_events - set(TYPES))]

conf = readme.split("## Configuration", 1)[-1].split("\n## ", 1)[0]
doc_env = first_cells(conf)
problems += [f"README lacks setting {n}" for n in sorted(settings - doc_env)]
problems += [f"README names setting {n} that does not exist" for n in sorted(doc_env - settings - extra_env)]

# 3. README JSON blocks and the MCP config
blocks = re.findall(r"```json\n(.*?)```", readme, re.S)
configs = []
for b in blocks:
    try:
        configs.append(json.loads(b))
    except ValueError as e:
        problems.append(f"README has a JSON block that does not parse: {e}")
servers = [s for c in configs for s in (c.get("mcpServers") or {}).values()]
if not servers:
    problems.append("README has no mcpServers config")
for s in servers:
    if not str(s.get("url", "")).endswith("/mcp"):
        problems.append("README MCP config url does not end in /mcp")
    if not str(s.get("headers", {}).get("Authorization", "")).startswith("Bearer "):
        problems.append("README MCP config has no Authorization: Bearer header")

# 4. state diagram against the transition table
diagram = next((b for b in re.findall(r"```mermaid\n(.*?)```", arch, re.S) if "stateDiagram" in b), "")
drawn = set(re.findall(r"^\s*(\w+)\s*-->\s*(\w+)", diagram, re.M))
truth = {(str(a), str(b)) for (a, _), b in TABLE.items()}
problems += [f"state diagram lacks {a} --> {b}" for a, b in sorted(truth - drawn)]
problems += [f"state diagram draws {a} --> {b} which is not a transition" for a, b in sorted(drawn - truth)]
if arch.count("```mermaid") < 4:
    problems.append("ARCHITECTURE.md needs the layers, state machine, event flow and MCP flow diagrams")

# 5. repo paths named in the docs
top = ("backend/", "scripts/", "frontend/", ".github/", "docs/", "compose.yaml", ".ai/")
names = ["README.md", "docs/API.md", "docs/REALTIME.md", "docs/ARCHITECTURE.md"] + sorted(
    str(p.relative_to(docs)) for p in (docs / ".ai").glob("*.md")
)
for rel in names:
    text = (docs / rel).read_text() if (docs / rel).is_file() else ""
    text = re.sub(r"<!-- planned -->.*?<!-- /planned -->", "", text, flags=re.S)
    for tok in set(re.findall(r"`([^`\s]+)`", text)) | set(re.findall(r"\]\(([^)#\s]+)", text)):
        tok = re.sub(r"[#:].*$", "", tok).rstrip("/.,")
        if tok.startswith(top) and not re.search(r"[<>*{}$]", tok) and not (repo / tok).exists() and not (docs / tok).exists():
            problems.append(f"{rel} names {tok}, which does not exist")

if problems:
    print("docs drift:\n  " + "\n  ".join(problems))
    sys.exit(1)
print("docs match the code")
PY

"""scripts/check_docs.sh: the docs match the code, and each kind of drift is caught."""

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).parents[3]
SCRIPT = REPO / "scripts" / "check_docs.sh"


def check(docs_root: Path) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, "DOCS_ROOT": str(docs_root), "DOCS_PYTHON": sys.executable}
    return subprocess.run(["bash", str(SCRIPT)], env=env, capture_output=True, text=True, check=False)


@pytest.fixture
def copy(tmp_path: Path) -> Path:
    shutil.copy(REPO / "README.md", tmp_path)
    shutil.copytree(REPO / "docs", tmp_path / "docs", ignore=shutil.ignore_patterns("plan.md", "spec.md", "design-options"))
    return tmp_path


def test_the_repo_docs_match_the_code() -> None:
    out = check(REPO)
    assert out.returncode == 0, out.stdout + out.stderr


def test_a_route_missing_from_api_md_fails(copy: Path) -> None:
    api = copy / "docs" / "API.md"
    api.write_text(api.read_text().replace("`POST /api/v1/agents`", "`POST /api/v1/agent-removed`"))
    out = check(copy)
    assert out.returncode != 0
    assert "lacks route POST /api/v1/agents" in out.stdout
    assert "agent-removed" in out.stdout  # and a documented route that does not exist


def test_a_broken_mcp_config_fails(copy: Path) -> None:
    readme = copy / "README.md"
    readme.write_text(readme.read_text().replace('"type": "http",', '"type": "http"'))
    out = check(copy)
    assert out.returncode != 0
    assert "does not parse" in out.stdout


def test_a_path_that_no_longer_exists_fails(copy: Path) -> None:
    readme = copy / "README.md"
    readme.write_text(readme.read_text() + "\nSee `backend/app/gone/module.py`.\n")
    out = check(copy)
    assert out.returncode != 0
    assert "backend/app/gone/module.py" in out.stdout


def test_a_state_diagram_that_lost_a_transition_fails(copy: Path) -> None:
    arch = copy / "docs" / "ARCHITECTURE.md"
    arch.write_text(arch.read_text().replace("pending_approval --> expired", "%% removed", 1))
    out = check(copy)
    assert out.returncode != 0
    assert "pending_approval --> expired" in out.stdout

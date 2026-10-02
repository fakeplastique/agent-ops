import os
import subprocess
from pathlib import Path

GATE = Path(__file__).parents[2] / "scripts" / "gate.sh"


def run_gate(tmp_path: Path, uv_exit: int = 0, curl: str = "stateful") -> int:
    # Fake tool binaries first on PATH: gate must propagate their exit codes.
    # curl "stateful": fails until the first probe is done, then answers (API came up after start).
    curls = {
        "up": "exit 0",
        "down": "exit 1",
        "stateful": f"[ -f {tmp_path}/seen ] && exit 0; touch {tmp_path}/seen; exit 1",
    }
    shims = {
        "uv": f"exit {uv_exit}",
        "pnpm": "exit 0",
        "npx": "exit 0",
        "docker": "exit 0",
        "curl": curls[curl],
    }
    for tool, body in shims.items():
        shim = tmp_path / tool
        shim.write_text(f"#!/bin/sh\n{body}\n")
        shim.chmod(0o755)
    env = {**os.environ, "PATH": f"{tmp_path}:{os.environ['PATH']}", "SKEW_CHECK": "true"}
    return subprocess.run(["bash", str(GATE)], check=False, env=env, capture_output=True).returncode


def test_gate_exits_nonzero_when_a_step_fails(tmp_path):
    assert run_gate(tmp_path, uv_exit=1) != 0


def test_gate_exits_zero_when_all_steps_pass(tmp_path):
    assert run_gate(tmp_path) == 0


def test_gate_fails_when_port_8000_already_answers(tmp_path):
    # A stale server would make newman and Playwright test the wrong code: false green.
    assert run_gate(tmp_path, curl="up") != 0


def test_gate_fails_when_the_api_never_comes_up(tmp_path):
    assert run_gate(tmp_path, curl="down") != 0

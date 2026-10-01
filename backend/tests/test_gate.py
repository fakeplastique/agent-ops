import os
import subprocess
from pathlib import Path

GATE = Path(__file__).parents[2] / "scripts" / "gate.sh"


def run_gate(tmp_path: Path, uv_exit: int) -> int:
    # Fake tool binaries first on PATH: gate must propagate their exit codes.
    for tool, code in {"uv": uv_exit, "pnpm": 0, "npx": 0, "docker": 0}.items():
        shim = tmp_path / tool
        shim.write_text(f"#!/bin/sh\nexit {code}\n")
        shim.chmod(0o755)
    env = {**os.environ, "PATH": f"{tmp_path}:{os.environ['PATH']}"}
    return subprocess.run(["bash", str(GATE)], check=False, env=env, capture_output=True).returncode


def test_gate_exits_nonzero_when_a_step_fails(tmp_path):
    assert run_gate(tmp_path, uv_exit=1) != 0


def test_gate_exits_zero_when_all_steps_pass(tmp_path):
    assert run_gate(tmp_path, uv_exit=0) == 0

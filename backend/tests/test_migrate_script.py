import os
import subprocess
from pathlib import Path

MIGRATE = Path(__file__).parents[2] / "scripts" / "migrate.sh"


def run_migrate(**env: str) -> subprocess.CompletedProcess[str]:
    base = {k: v for k, v in os.environ.items() if k != "APP_RT_PASSWORD"}
    # MIGRATE_PYTHON=false: if the guard did not stop us, the python step fails differently.
    return subprocess.run(
        ["bash", str(MIGRATE)],
        check=False,
        capture_output=True,
        text=True,
        env={**base, "MIGRATE_DATABASE_URL": "postgresql://x/x", "MIGRATE_PYTHON": "false", **env},
    )


def test_hosted_environment_without_app_rt_password_refuses_before_touching_the_db():
    # Catches: the dev password `app_rt` silently surviving in production.
    r = run_migrate(RAILWAY_ENVIRONMENT="production")
    assert r.returncode != 0
    assert "APP_RT_PASSWORD" in r.stderr


def test_hosted_environment_with_password_passes_the_guard():
    r = run_migrate(RAILWAY_ENVIRONMENT="production", APP_RT_PASSWORD="x")
    assert "APP_RT_PASSWORD" not in r.stderr  # fails later on the fake python, not on the guard

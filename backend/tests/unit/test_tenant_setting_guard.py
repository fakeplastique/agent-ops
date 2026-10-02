from pathlib import Path

APP = Path(__file__).parents[2] / "app"
ALLOWED = {APP / "db" / "session.py"}


def test_only_session_py_sets_the_tenant_setting():
    # The tenant wall is a GUC the client can change; keep every writer in one reviewed place.
    offenders = [
        str(p.relative_to(APP))
        for p in APP.rglob("*.py")
        if "migrations" not in p.parts
        and p not in ALLOWED
        and ("set_config" in p.read_text() or "app.organization_id" in p.read_text())
    ]
    assert offenders == []

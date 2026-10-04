from pathlib import Path

APP = Path(__file__).parents[2] / "app"
SESSION = APP / "db" / "session.py"
RESOLVER = (
    APP / "authz" / "resolver.py"
)  # sets app.lookup_prefix: the credential lookup before a tenant is known


def _sources():
    return [p for p in APP.rglob("*.py") if "migrations" not in p.parts]


def test_only_session_py_sets_the_tenant_setting():
    # The tenant wall is a GUC the client can change; keep every writer in one reviewed place.
    offenders = [
        str(p.relative_to(APP))
        for p in _sources()
        if p != SESSION and "app.organization_id" in p.read_text()
    ]
    assert offenders == []


def test_set_config_lives_in_session_py_and_the_resolver_only_and_the_resolver_never_names_the_tenant():
    offenders = [
        str(p.relative_to(APP))
        for p in _sources()
        if p not in (SESSION, RESOLVER) and "set_config" in p.read_text()
    ]
    assert offenders == []
    text = RESOLVER.read_text()
    assert "app.lookup_prefix" in text and "app.organization_id" not in text

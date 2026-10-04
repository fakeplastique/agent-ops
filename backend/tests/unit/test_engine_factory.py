from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.config import settings
from app.db.engine import make_engine

APP = Path(__file__).parents[2] / "app"


def test_every_app_engine_comes_from_the_one_factory():
    factory = APP / "db" / "engine.py"
    offenders = [
        str(p.relative_to(APP))
        for p in APP.rglob("*.py")
        if p != factory
        and "create_async_engine(" in p.read_text()  # catches: an engine that logs bound parameters
    ]
    assert offenders == []


@pytest.mark.anyio
async def test_a_failed_statement_does_not_carry_its_parameters_into_the_error():
    engine = make_engine(settings.database_url)
    try:
        with pytest.raises(DBAPIError) as err:
            async with engine.connect() as conn:
                await conn.execute(text("selec :payload"), {"payload": "SECRET-PAYLOAD-123"})
    finally:
        await engine.dispose()
    # D21: logs and error bodies never contain payloads. SQLAlchemy would print [parameters: (...)] here.
    message = str(err.value)
    assert (
        "SECRET-PAYLOAD-123" not in message
        and "[parameters:" not in message
        and "hidden" in message
    )

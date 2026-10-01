from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy.engine import Connection


def upgrade(connection: Connection, revision: str = "head") -> None:
    # Programmatic config: no alembic.ini; env.py reuses the caller's connection.
    cfg = Config()
    cfg.set_main_option("script_location", str(Path(__file__).parent / "migrations"))
    cfg.attributes["connection"] = connection
    command.upgrade(cfg, revision)

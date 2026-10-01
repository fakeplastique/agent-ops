from pydantic import field_validator
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    database_url: str = "postgresql+asyncpg://app:app@localhost:5432/agent_ops"
    redis_url: str = "redis://localhost:6379/0"

    @field_validator("database_url")
    @classmethod
    def _asyncpg_driver(cls, v: str) -> str:
        # Hosts (Railway) hand out postgresql://, which SQLAlchemy maps to the sync psycopg2.
        return v.replace("postgresql://", "postgresql+asyncpg://", 1)


settings = Settings()

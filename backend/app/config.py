import os

from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings

from app.domain.receipt import KeyRing

DEV_RECEIPT_KEYS = "dev:dev-receipt-key-not-for-production"


class Settings(BaseSettings):
    # Runtime role (non-owner, RLS applies); migrations use the owner URL.
    database_url: str = "postgresql+asyncpg://app_rt:app_rt@localhost:5432/agent_ops"
    migrate_database_url: str = "postgresql+asyncpg://app:app@localhost:5432/agent_ops"
    redis_url: str = "redis://localhost:6379/0"
    # `kid:secret,kid:secret`; the last signs, all verify. Hosted runs must set their own.
    receipt_keys: str = DEV_RECEIPT_KEYS

    @field_validator("database_url", "migrate_database_url")
    @classmethod
    def _asyncpg_driver(cls, v: str) -> str:
        # Hosts (Railway) hand out postgresql://, which SQLAlchemy maps to the sync psycopg2.
        return v.replace("postgresql://", "postgresql+asyncpg://", 1)

    @model_validator(mode="after")
    def _receipt_keys_usable(self) -> "Settings":
        KeyRing.parse(self.receipt_keys)  # fail at boot, not on the first approval
        if os.environ.get("RAILWAY_ENVIRONMENT") and self.receipt_keys == DEV_RECEIPT_KEYS:
            raise ValueError(
                "RECEIPT_KEYS must be set on Railway: the dev key makes receipts forgeable"
            )
        return self


settings = Settings()

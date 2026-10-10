import os

from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings

from app.domain.receipt import KeyRing
from app.domain.secretbox import SecretBox

DEV_RECEIPT_KEYS = "dev:dev-receipt-key-not-for-production"
# base64 of "dev-webhook-key-not-for-prod-use" (32 bytes)
DEV_WEBHOOK_SECRET_KEYS = "dev:ZGV2LXdlYmhvb2sta2V5LW5vdC1mb3ItcHJvZC11c2U="


class Settings(BaseSettings):
    # Runtime role (non-owner, RLS applies); migrations use the owner URL.
    database_url: str = "postgresql+asyncpg://app_rt:app_rt@localhost:5432/agent_ops"
    migrate_database_url: str = "postgresql+asyncpg://app:app@localhost:5432/agent_ops"
    # The timeout worker's scan identity (ids of due work only). NOLOGIN until an environment provisions it.
    worker_database_url: str = "postgresql+asyncpg://app_worker:app_worker@localhost:5432/agent_ops"
    redis_url: str = "redis://localhost:6379/0"
    # Fixed one-minute windows (D22). Writes: per principal and per organization; failed bearer auth: per IP.
    write_limit_per_principal: int = 60
    write_limit_per_org: int = 300
    auth_failures_per_minute: int = 20
    # Web login (D7): Google OIDC. The origin is the web app's (the API is reached through its rewrites), so the
    # redirect URI, the cookie and the Origin check all live on it. Empty client id = login unavailable.
    web_origin: str = "http://localhost:3000"
    oidc_issuer: str = "https://accounts.google.com"
    oidc_client_id: str = ""
    oidc_client_secret: str = ""
    # Comma-separated public hostnames the MCP endpoint answers on (else the SDK's rebinding guard says 421).
    mcp_allowed_hosts: str = ""
    # `kid:secret,kid:secret`; the last signs, all verify. Hosted runs must set their own.
    receipt_keys: str = DEV_RECEIPT_KEYS
    # Org admins may switch their own plan only in a demo deployment (spec 2).
    demo_mode: bool = False
    # Comma-separated emails; the only source of the platform-admin role (users.platform_admin is not read).
    platform_admin_emails: str = ""
    # Shared secret a scraper sends as X-Metrics-Token to read GET /metrics; empty = no metrics route.
    metrics_token: str = ""
    # Webhook targets must be https; plain http only for a local receiver in dev (refused on Railway).
    webhooks_allow_http: bool = False
    # `kid:base64(32 bytes),...` (AES-256-GCM for stored webhook secrets); the last seals, all open.
    webhook_secret_keys: str = DEV_WEBHOOK_SECRET_KEYS
    # Telegram (D19). No token = nothing is sent; no webhook secret = the webhook answers 404.
    telegram_bot_token: str = ""
    telegram_webhook_secret: str = ""
    telegram_bot_username: str = ""  # for the t.me deep link

    @field_validator("database_url", "migrate_database_url", "worker_database_url")
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
        # By key bytes, not spelling: a renamed kid or a stray comma still carries the public key.
        dev_key = SecretBox.parse(DEV_WEBHOOK_SECRET_KEYS).keys["dev"]
        ring = SecretBox.parse(self.webhook_secret_keys).keys.values()
        if os.environ.get("RAILWAY_ENVIRONMENT") and dev_key in ring:
            raise ValueError(
                "WEBHOOK_SECRET_KEYS must be set on Railway: the dev key is public in git"
            )
        if os.environ.get("RAILWAY_ENVIRONMENT") and self.webhooks_allow_http:
            raise ValueError("WEBHOOKS_ALLOW_HTTP is for local dev: hosted webhooks are https only")
        return self


settings = Settings()

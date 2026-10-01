from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    database_url: str = "postgresql+asyncpg://app:app@localhost:5432/agent_ops"
    redis_url: str = "redis://localhost:6379/0"


settings = Settings()

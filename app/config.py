from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_host: str = "127.0.0.1"
    app_port: int = 8000

    database_url: str = (
        "postgresql+asyncpg://postgres:postgres@localhost:5432/cottage_launcher"
    )
    redis_url: str = "redis://localhost:6379/0"

    secret_key: str = "dev-secret"
    modrinth_user_agent: str = (
        "CottageLauncher/0.1 (+https://github.com/your-org/cottage-launcher)"
    )

    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="",
        case_sensitive=False,
        extra="ignore",
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()

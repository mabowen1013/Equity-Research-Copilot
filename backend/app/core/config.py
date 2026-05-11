from functools import lru_cache
from pathlib import Path

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DATABASE_URL = (
    "postgresql+psycopg://"
    "equity_research:equity_research_password"
    "@localhost:5432/equity_research_copilot"
)


class Settings(BaseSettings):
    database_url: str = DEFAULT_DATABASE_URL
    sec_user_agent: str | None = None
    openai_api_key: SecretStr | None = None

    model_config = SettingsConfigDict(
        env_file=BACKEND_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()

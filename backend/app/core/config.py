from functools import lru_cache
from pathlib import Path

from typing import Literal

from pydantic import Field, SecretStr
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
    sec_rate_limit_per_second: int = Field(default=10, ge=1, le=10)
    sec_cache_ttl_seconds: int = Field(default=86_400, ge=1)
    openai_api_key: SecretStr | None = None
    embedding_provider: str = "openai"
    embedding_model: str = "text-embedding-3-small"
    embedding_dimensions: int = Field(default=1536, ge=1)
    embedding_input_version: str = "v1"
    vector_search_mode: Literal["exact", "hnsw", "auto"] = "exact"
    retrieval_dense_candidates: int = Field(default=40, ge=1, le=500)
    retrieval_lexical_candidates: int = Field(default=40, ge=1, le=500)
    retrieval_fact_candidates: int = Field(default=20, ge=1, le=500)
    retrieval_top_k: int = Field(default=10, ge=1, le=50)
    query_planner_mode: Literal["rule_only", "rule_with_llm_fallback"] = "rule_only"
    query_planner_llm_model: str = "gpt-4o-mini"
    query_planner_llm_confidence_threshold: float = Field(default=0.75, ge=0, le=1)
    query_planner_llm_timeout_seconds: float = Field(default=8.0, gt=0, le=60)

    model_config = SettingsConfigDict(
        env_file=BACKEND_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()


def get_required_sec_user_agent(settings: Settings | None = None) -> str:
    active_settings = settings or get_settings()
    user_agent = active_settings.sec_user_agent

    if user_agent is None or not user_agent.strip():
        raise RuntimeError("SEC_USER_AGENT must be configured before making SEC requests.")

    return user_agent.strip()

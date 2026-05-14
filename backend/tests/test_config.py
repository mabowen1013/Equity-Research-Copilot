import pytest

from app.core.config import Settings, get_required_sec_user_agent, get_settings


def test_settings_load_sec_user_agent_and_openai_key_from_env_file(tmp_path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join(
            [
                "SEC_USER_AGENT=Equity Research Copilot test contact@example.com",
                "SEC_RATE_LIMIT_PER_SECOND=8",
                "SEC_CACHE_TTL_SECONDS=3600",
                f"SEC_FILING_CACHE_DIR={tmp_path / 'sec-filings'}",
                "OPENAI_API_KEY=sk-test",
                "DATABASE_URL=postgresql+psycopg://user:pass@localhost:5432/test_db",
            ]
        ),
        encoding="utf-8",
    )

    settings = Settings(_env_file=env_file)

    assert settings.database_url == "postgresql+psycopg://user:pass@localhost:5432/test_db"
    assert settings.sec_user_agent == "Equity Research Copilot test contact@example.com"
    assert settings.sec_rate_limit_per_second == 8
    assert settings.sec_cache_ttl_seconds == 3600
    assert settings.sec_filing_cache_dir == tmp_path / "sec-filings"
    assert settings.openai_api_key is not None
    assert settings.openai_api_key.get_secret_value() == "sk-test"


def test_settings_default_sec_rate_limit_and_cache_ttl(monkeypatch) -> None:
    monkeypatch.delenv("SEC_RATE_LIMIT_PER_SECOND", raising=False)
    monkeypatch.delenv("SEC_CACHE_TTL_SECONDS", raising=False)
    monkeypatch.delenv("SEC_FILING_CACHE_DIR", raising=False)

    settings = Settings(_env_file=None)

    assert settings.sec_rate_limit_per_second == 10
    assert settings.sec_cache_ttl_seconds == 86_400
    assert settings.sec_filing_cache_dir.name == "sec_filings"


def test_required_sec_user_agent_returns_trimmed_value() -> None:
    settings = Settings(sec_user_agent="  Equity Research Copilot contact@example.com  ")

    assert get_required_sec_user_agent(settings) == "Equity Research Copilot contact@example.com"


def test_required_sec_user_agent_fails_when_missing() -> None:
    settings = Settings(sec_user_agent=" ")

    with pytest.raises(RuntimeError, match="SEC_USER_AGENT must be configured"):
        get_required_sec_user_agent(settings)


def test_get_settings_returns_cached_settings_instance() -> None:
    get_settings.cache_clear()

    assert get_settings() is get_settings()

    get_settings.cache_clear()

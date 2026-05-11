from app.core.config import Settings, get_settings


def test_settings_load_sec_user_agent_and_openai_key_from_env_file(tmp_path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join(
            [
                "SEC_USER_AGENT=Equity Research Copilot test contact@example.com",
                "OPENAI_API_KEY=sk-test",
                "DATABASE_URL=postgresql+psycopg://user:pass@localhost:5432/test_db",
            ]
        ),
        encoding="utf-8",
    )

    settings = Settings(_env_file=env_file)

    assert settings.database_url == "postgresql+psycopg://user:pass@localhost:5432/test_db"
    assert settings.sec_user_agent == "Equity Research Copilot test contact@example.com"
    assert settings.openai_api_key is not None
    assert settings.openai_api_key.get_secret_value() == "sk-test"


def test_get_settings_returns_cached_settings_instance() -> None:
    get_settings.cache_clear()

    assert get_settings() is get_settings()

    get_settings.cache_clear()

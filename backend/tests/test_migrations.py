from configparser import ConfigParser
from pathlib import Path

from app.db.base import Base


def test_alembic_config_points_to_migration_directory() -> None:
    config_path = Path(__file__).resolve().parents[1] / "alembic.ini"
    config = ConfigParser()

    assert config.read(config_path)
    assert config["alembic"]["script_location"] == "alembic"


def test_database_metadata_is_available_for_migrations() -> None:
    assert "jobs" in Base.metadata.tables
    assert "companies" in Base.metadata.tables
    assert "filings" in Base.metadata.tables
    assert "filing_documents" in Base.metadata.tables
    assert "filing_sections" in Base.metadata.tables
    assert "document_chunks" in Base.metadata.tables
    assert "sec_response_cache" in Base.metadata.tables

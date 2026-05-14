from configparser import ConfigParser
from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory

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
    assert "sec_response_cache" in Base.metadata.tables
    assert "filing_documents" in Base.metadata.tables
    assert "filing_sections" in Base.metadata.tables
    assert "document_chunks" in Base.metadata.tables


def test_alembic_revision_ids_fit_default_version_table() -> None:
    config_path = Path(__file__).resolve().parents[1] / "alembic.ini"
    config = Config(str(config_path))
    config.set_main_option("script_location", str(config_path.parent / "alembic"))
    script = ScriptDirectory.from_config(config)

    for revision in script.walk_revisions():
        assert len(revision.revision) <= 32

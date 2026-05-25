from configparser import ConfigParser
import importlib.util
from pathlib import Path

from app import models  # noqa: F401
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
    assert "chunk_embeddings" in Base.metadata.tables
    assert "financial_facts" in Base.metadata.tables
    assert "sec_response_cache" in Base.metadata.tables


def test_alembic_revision_ids_fit_version_table() -> None:
    versions_dir = Path(__file__).resolve().parents[1] / "alembic" / "versions"
    for migration_path in versions_dir.glob("*.py"):
        if migration_path.name == "__init__.py":
            continue
        spec = importlib.util.spec_from_file_location(migration_path.stem, migration_path)
        assert spec is not None
        module = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        spec.loader.exec_module(module)
        assert len(module.revision) <= 32, migration_path.name

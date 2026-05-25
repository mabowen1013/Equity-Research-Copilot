from app.db.base import Base
from app.models import ChunkEmbedding


def test_chunk_embeddings_table_contains_versioned_vector_columns() -> None:
    columns = ChunkEmbedding.__table__.columns

    assert set(columns.keys()) == {
        "id",
        "chunk_id",
        "company_id",
        "filing_id",
        "provider",
        "model",
        "dimensions",
        "embedding_input_version",
        "content_sha256",
        "embedding",
        "created_at",
        "updated_at",
    }


def test_chunk_embeddings_table_defines_current_version_unique_constraint() -> None:
    constraints = {
        constraint.name: {column.name for column in constraint.columns}
        for constraint in ChunkEmbedding.__table__.constraints
        if constraint.name
    }

    assert constraints["uq_chunk_embeddings_current_version"] == {
        "chunk_id",
        "provider",
        "model",
        "dimensions",
        "embedding_input_version",
    }


def test_chunk_embeddings_table_is_registered_for_migrations() -> None:
    assert "chunk_embeddings" in Base.metadata.tables

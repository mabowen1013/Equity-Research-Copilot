"""create hnsw index for chunk embedding cosine search

Revision ID: 0008_hnsw_index
Revises: 0007_research_runs
Create Date: 2026-06-11
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0008_hnsw_index"
down_revision: str | Sequence[str] | None = "0007_research_runs"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Cosine ops matches the <=> operator used by dense retrieval. m/ef_construction
    # use pgvector defaults; ef_search is set per transaction by RetrievalService.
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_chunk_embeddings_embedding_hnsw
        ON chunk_embeddings
        USING hnsw (embedding vector_cosine_ops)
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_chunk_embeddings_embedding_hnsw")

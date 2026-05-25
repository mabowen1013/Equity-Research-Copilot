"""create chunk embeddings and lexical search

Revision ID: 0005_chunk_embeddings
Revises: 0004_create_financial_facts
Create Date: 2026-05-20
"""

from collections.abc import Sequence
from typing import Any

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from sqlalchemy.types import UserDefinedType

revision: str = "0005_chunk_embeddings"
down_revision: str | Sequence[str] | None = "0004_create_financial_facts"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


class Vector(UserDefinedType):
    cache_ok = True

    def __init__(self, dimensions: int) -> None:
        self.dimensions = dimensions

    def get_col_spec(self, **kw: Any) -> str:
        return f"vector({self.dimensions})"


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    op.add_column(
        "document_chunks",
        sa.Column(
            "search_vector",
            postgresql.TSVECTOR(),
            sa.Computed(
                "setweight(to_tsvector('english', coalesce(section_label, '')), 'A') || "
                "setweight(to_tsvector('english', coalesce(chunk_text, '')), 'B')",
                persisted=True,
            ),
            nullable=True,
        ),
    )
    op.create_index(
        "ix_document_chunks_search_vector",
        "document_chunks",
        ["search_vector"],
        postgresql_using="gin",
    )

    op.create_table(
        "chunk_embeddings",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("chunk_id", sa.Integer(), nullable=False),
        sa.Column("company_id", sa.Integer(), nullable=False),
        sa.Column("filing_id", sa.Integer(), nullable=False),
        sa.Column("provider", sa.String(length=64), nullable=False),
        sa.Column("model", sa.String(length=128), nullable=False),
        sa.Column("dimensions", sa.Integer(), nullable=False),
        sa.Column("embedding_input_version", sa.String(length=32), nullable=False),
        sa.Column("content_sha256", sa.String(length=64), nullable=False),
        sa.Column("embedding", Vector(1536), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["chunk_id"], ["document_chunks.id"]),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"]),
        sa.ForeignKeyConstraint(["filing_id"], ["filings.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "chunk_id",
            "provider",
            "model",
            "dimensions",
            "embedding_input_version",
            name="uq_chunk_embeddings_current_version",
        ),
    )
    op.create_index("ix_chunk_embeddings_chunk_id", "chunk_embeddings", ["chunk_id"])
    op.create_index("ix_chunk_embeddings_company_id", "chunk_embeddings", ["company_id"])
    op.create_index("ix_chunk_embeddings_filing_id", "chunk_embeddings", ["filing_id"])
    op.create_index("ix_chunk_embeddings_model", "chunk_embeddings", ["model"])
    op.create_index(
        "ix_chunk_embeddings_embedding_input_version",
        "chunk_embeddings",
        ["embedding_input_version"],
    )
    op.create_index("ix_chunk_embeddings_provider", "chunk_embeddings", ["provider"])


def downgrade() -> None:
    op.drop_index("ix_chunk_embeddings_provider", table_name="chunk_embeddings")
    op.drop_index("ix_chunk_embeddings_embedding_input_version", table_name="chunk_embeddings")
    op.drop_index("ix_chunk_embeddings_model", table_name="chunk_embeddings")
    op.drop_index("ix_chunk_embeddings_filing_id", table_name="chunk_embeddings")
    op.drop_index("ix_chunk_embeddings_company_id", table_name="chunk_embeddings")
    op.drop_index("ix_chunk_embeddings_chunk_id", table_name="chunk_embeddings")
    op.drop_table("chunk_embeddings")

    op.drop_index("ix_document_chunks_search_vector", table_name="document_chunks")
    op.drop_column("document_chunks", "search_vector")

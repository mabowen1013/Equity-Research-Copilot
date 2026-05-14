"""create filing processing tables

Revision ID: 0003_filing_processing_tables
Revises: 0002_create_sec_ingestion_tables
Create Date: 2026-05-14
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "0003_filing_processing_tables"
down_revision: str | Sequence[str] | None = "0002_create_sec_ingestion_tables"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "filing_documents",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("filing_id", sa.Integer(), nullable=False),
        sa.Column("source_url", sa.Text(), nullable=False),
        sa.Column("cache_path", sa.Text(), nullable=True),
        sa.Column("content_sha256", sa.String(length=64), nullable=True),
        sa.Column("content_type", sa.String(length=128), nullable=True),
        sa.Column("byte_size", sa.Integer(), nullable=True),
        sa.Column(
            "status",
            sa.String(length=32),
            server_default=sa.text("'pending'"),
            nullable=False,
        ),
        sa.Column("parser_version", sa.String(length=64), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("downloaded_at", sa.DateTime(timezone=True), nullable=True),
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
        sa.CheckConstraint(
            "status IN ('pending', 'downloaded', 'failed')",
            name="ck_filing_documents_status",
        ),
        sa.CheckConstraint(
            "byte_size IS NULL OR byte_size >= 0",
            name="ck_filing_documents_byte_size_nonnegative",
        ),
        sa.ForeignKeyConstraint(["filing_id"], ["filings.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("filing_id", name="uq_filing_documents_filing_id"),
    )
    op.create_index(
        "ix_filing_documents_filing_id",
        "filing_documents",
        ["filing_id"],
    )
    op.create_index("ix_filing_documents_status", "filing_documents", ["status"])

    op.create_table(
        "filing_sections",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("filing_id", sa.Integer(), nullable=False),
        sa.Column("section_key", sa.String(length=64), nullable=False),
        sa.Column("section_title", sa.Text(), nullable=False),
        sa.Column("section_order", sa.Integer(), nullable=False),
        sa.Column("normalized_text", sa.Text(), nullable=False),
        sa.Column("start_offset", sa.Integer(), nullable=False),
        sa.Column("end_offset", sa.Integer(), nullable=False),
        sa.Column("extraction_confidence", sa.Integer(), nullable=False),
        sa.Column("extraction_method", sa.String(length=64), nullable=False),
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
        sa.CheckConstraint(
            "section_order >= 0",
            name="ck_filing_sections_order_nonnegative",
        ),
        sa.CheckConstraint(
            "start_offset >= 0 AND end_offset >= start_offset",
            name="ck_filing_sections_offsets",
        ),
        sa.CheckConstraint(
            "extraction_confidence >= 0 AND extraction_confidence <= 100",
            name="ck_filing_sections_confidence_range",
        ),
        sa.ForeignKeyConstraint(["filing_id"], ["filings.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("filing_id", "section_key", name="uq_filing_sections_key"),
        sa.UniqueConstraint("filing_id", "section_order", name="uq_filing_sections_order"),
    )
    op.create_index("ix_filing_sections_filing_id", "filing_sections", ["filing_id"])
    op.create_index("ix_filing_sections_section_key", "filing_sections", ["section_key"])

    op.create_table(
        "document_chunks",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("filing_id", sa.Integer(), nullable=False),
        sa.Column("section_id", sa.Integer(), nullable=False),
        sa.Column("chunk_index", sa.Integer(), nullable=False),
        sa.Column("chunk_text", sa.Text(), nullable=False),
        sa.Column("token_count", sa.Integer(), nullable=False),
        sa.Column("start_offset", sa.Integer(), nullable=False),
        sa.Column("end_offset", sa.Integer(), nullable=False),
        sa.Column("text_hash", sa.String(length=64), nullable=False),
        sa.Column("accession_number", sa.String(length=32), nullable=False),
        sa.Column("form_type", sa.String(length=16), nullable=False),
        sa.Column("filing_date", sa.Date(), nullable=False),
        sa.Column("section_key", sa.String(length=64), nullable=False),
        sa.Column("sec_url", sa.Text(), nullable=False),
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
        sa.CheckConstraint(
            "chunk_index >= 0",
            name="ck_document_chunks_index_nonnegative",
        ),
        sa.CheckConstraint(
            "token_count > 0",
            name="ck_document_chunks_token_count_positive",
        ),
        sa.CheckConstraint(
            "start_offset >= 0 AND end_offset >= start_offset",
            name="ck_document_chunks_offsets",
        ),
        sa.ForeignKeyConstraint(["filing_id"], ["filings.id"]),
        sa.ForeignKeyConstraint(["section_id"], ["filing_sections.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "section_id",
            "chunk_index",
            name="uq_document_chunks_section_index",
        ),
    )
    op.create_index("ix_document_chunks_filing_id", "document_chunks", ["filing_id"])
    op.create_index("ix_document_chunks_section_id", "document_chunks", ["section_id"])
    op.create_index(
        "ix_document_chunks_accession_number",
        "document_chunks",
        ["accession_number"],
    )
    op.create_index("ix_document_chunks_form_type", "document_chunks", ["form_type"])
    op.create_index("ix_document_chunks_filing_date", "document_chunks", ["filing_date"])
    op.create_index("ix_document_chunks_section_key", "document_chunks", ["section_key"])


def downgrade() -> None:
    op.drop_index("ix_document_chunks_section_key", table_name="document_chunks")
    op.drop_index("ix_document_chunks_filing_date", table_name="document_chunks")
    op.drop_index("ix_document_chunks_form_type", table_name="document_chunks")
    op.drop_index("ix_document_chunks_accession_number", table_name="document_chunks")
    op.drop_index("ix_document_chunks_section_id", table_name="document_chunks")
    op.drop_index("ix_document_chunks_filing_id", table_name="document_chunks")
    op.drop_table("document_chunks")

    op.drop_index("ix_filing_sections_section_key", table_name="filing_sections")
    op.drop_index("ix_filing_sections_filing_id", table_name="filing_sections")
    op.drop_table("filing_sections")

    op.drop_index("ix_filing_documents_status", table_name="filing_documents")
    op.drop_index("ix_filing_documents_filing_id", table_name="filing_documents")
    op.drop_table("filing_documents")

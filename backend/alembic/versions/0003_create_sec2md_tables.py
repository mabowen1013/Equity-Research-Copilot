"""create sec2md filing parse tables

Revision ID: 0003_create_sec2md_tables
Revises: 0002_create_sec_ingestion_tables
Create Date: 2026-05-16
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "0003_create_sec2md_tables"
down_revision: str | Sequence[str] | None = "0002_create_sec_ingestion_tables"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "filing_documents",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("filing_id", sa.Integer(), nullable=False),
        sa.Column("raw_html", sa.Text(), nullable=False),
        sa.Column("annotated_html", sa.Text(), nullable=True),
        sa.Column("source_url", sa.Text(), nullable=False),
        sa.Column("content_sha256", sa.String(length=64), nullable=False),
        sa.Column("parser_name", sa.String(length=64), server_default="sec2md", nullable=False),
        sa.Column("parser_version", sa.String(length=32), nullable=False),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("parsed_at", sa.DateTime(timezone=True), nullable=True),
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
        sa.ForeignKeyConstraint(["filing_id"], ["filings.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("filing_id", name="uq_filing_documents_filing_id"),
    )
    op.create_index("ix_filing_documents_content_sha256", "filing_documents", ["content_sha256"])
    op.create_index("ix_filing_documents_filing_id", "filing_documents", ["filing_id"])

    op.create_table(
        "filing_sections",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("filing_id", sa.Integer(), nullable=False),
        sa.Column("section_key", sa.String(length=128), nullable=False),
        sa.Column("part", sa.String(length=64), nullable=True),
        sa.Column("item", sa.String(length=64), nullable=True),
        sa.Column("title", sa.Text(), nullable=True),
        sa.Column("section_order", sa.Integer(), nullable=False),
        sa.Column("start_page", sa.Integer(), nullable=True),
        sa.Column("end_page", sa.Integer(), nullable=True),
        sa.Column("start_display_page", sa.Integer(), nullable=True),
        sa.Column("end_display_page", sa.Integer(), nullable=True),
        sa.Column("markdown_text", sa.Text(), nullable=False),
        sa.Column("token_count", sa.Integer(), server_default="0", nullable=False),
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
        sa.ForeignKeyConstraint(["filing_id"], ["filings.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_filing_sections_filing_id", "filing_sections", ["filing_id"])
    op.create_index("ix_filing_sections_section_key", "filing_sections", ["section_key"])
    op.create_index("ix_filing_sections_section_order", "filing_sections", ["section_order"])

    op.create_table(
        "document_chunks",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("filing_id", sa.Integer(), nullable=False),
        sa.Column("section_id", sa.Integer(), nullable=False),
        sa.Column("chunk_index", sa.Integer(), nullable=False),
        sa.Column("chunk_text", sa.Text(), nullable=False),
        sa.Column("token_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("accession_number", sa.String(length=32), nullable=False),
        sa.Column("form_type", sa.String(length=16), nullable=False),
        sa.Column("filing_date", sa.Date(), nullable=False),
        sa.Column("section_label", sa.Text(), nullable=False),
        sa.Column("sec_url", sa.Text(), nullable=False),
        sa.Column("start_page", sa.Integer(), nullable=True),
        sa.Column("end_page", sa.Integer(), nullable=True),
        sa.Column("start_display_page", sa.Integer(), nullable=True),
        sa.Column("end_display_page", sa.Integer(), nullable=True),
        sa.Column(
            "element_ids",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "xbrl_tags",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column("source_start_offset", sa.Integer(), nullable=True),
        sa.Column("source_end_offset", sa.Integer(), nullable=True),
        sa.Column("has_table", sa.Boolean(), server_default=sa.text("false"), nullable=False),
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
        sa.ForeignKeyConstraint(["filing_id"], ["filings.id"]),
        sa.ForeignKeyConstraint(["section_id"], ["filing_sections.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_document_chunks_accession_number", "document_chunks", ["accession_number"])
    op.create_index("ix_document_chunks_chunk_index", "document_chunks", ["chunk_index"])
    op.create_index("ix_document_chunks_filing_date", "document_chunks", ["filing_date"])
    op.create_index("ix_document_chunks_filing_id", "document_chunks", ["filing_id"])
    op.create_index("ix_document_chunks_form_type", "document_chunks", ["form_type"])
    op.create_index("ix_document_chunks_section_id", "document_chunks", ["section_id"])


def downgrade() -> None:
    op.drop_index("ix_document_chunks_section_id", table_name="document_chunks")
    op.drop_index("ix_document_chunks_form_type", table_name="document_chunks")
    op.drop_index("ix_document_chunks_filing_id", table_name="document_chunks")
    op.drop_index("ix_document_chunks_filing_date", table_name="document_chunks")
    op.drop_index("ix_document_chunks_chunk_index", table_name="document_chunks")
    op.drop_index("ix_document_chunks_accession_number", table_name="document_chunks")
    op.drop_table("document_chunks")

    op.drop_index("ix_filing_sections_section_order", table_name="filing_sections")
    op.drop_index("ix_filing_sections_section_key", table_name="filing_sections")
    op.drop_index("ix_filing_sections_filing_id", table_name="filing_sections")
    op.drop_table("filing_sections")

    op.drop_index("ix_filing_documents_filing_id", table_name="filing_documents")
    op.drop_index("ix_filing_documents_content_sha256", table_name="filing_documents")
    op.drop_table("filing_documents")

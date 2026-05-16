"""add sec2md page and citation metadata

Revision ID: 0005_sec2md_metadata
Revises: 0004_chunk_section_cascade
Create Date: 2026-05-16
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "0005_sec2md_metadata"
down_revision: str | Sequence[str] | None = "0004_chunk_section_cascade"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("filing_sections", sa.Column("page_start", sa.Integer(), nullable=True))
    op.add_column("filing_sections", sa.Column("page_end", sa.Integer(), nullable=True))
    op.add_column("filing_sections", sa.Column("display_page_start", sa.Integer(), nullable=True))
    op.add_column("filing_sections", sa.Column("display_page_end", sa.Integer(), nullable=True))

    op.add_column("document_chunks", sa.Column("page_start", sa.Integer(), nullable=True))
    op.add_column("document_chunks", sa.Column("page_end", sa.Integer(), nullable=True))
    op.add_column("document_chunks", sa.Column("display_page_start", sa.Integer(), nullable=True))
    op.add_column("document_chunks", sa.Column("display_page_end", sa.Integer(), nullable=True))
    op.add_column("document_chunks", sa.Column("element_ids", sa.JSON(), nullable=True))
    op.add_column("document_chunks", sa.Column("xbrl_tags", sa.JSON(), nullable=True))
    op.add_column(
        "document_chunks",
        sa.Column("has_table", sa.Boolean(), server_default=sa.text("false"), nullable=False),
    )
    op.add_column(
        "document_chunks",
        sa.Column("has_image", sa.Boolean(), server_default=sa.text("false"), nullable=False),
    )


def downgrade() -> None:
    op.drop_column("document_chunks", "has_image")
    op.drop_column("document_chunks", "has_table")
    op.drop_column("document_chunks", "xbrl_tags")
    op.drop_column("document_chunks", "element_ids")
    op.drop_column("document_chunks", "display_page_end")
    op.drop_column("document_chunks", "display_page_start")
    op.drop_column("document_chunks", "page_end")
    op.drop_column("document_chunks", "page_start")

    op.drop_column("filing_sections", "display_page_end")
    op.drop_column("filing_sections", "display_page_start")
    op.drop_column("filing_sections", "page_end")
    op.drop_column("filing_sections", "page_start")

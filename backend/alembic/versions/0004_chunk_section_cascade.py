"""cascade document chunks when filing sections are deleted

Revision ID: 0004_chunk_section_cascade
Revises: 0003_filing_processing_tables
Create Date: 2026-05-15
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0004_chunk_section_cascade"
down_revision: str | Sequence[str] | None = "0003_filing_processing_tables"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

DOCUMENT_CHUNKS_SECTION_ID_FK = "document_chunks_section_id_fkey"


def upgrade() -> None:
    op.drop_constraint(
        DOCUMENT_CHUNKS_SECTION_ID_FK,
        "document_chunks",
        type_="foreignkey",
    )
    op.create_foreign_key(
        DOCUMENT_CHUNKS_SECTION_ID_FK,
        "document_chunks",
        "filing_sections",
        ["section_id"],
        ["id"],
        ondelete="CASCADE",
    )


def downgrade() -> None:
    op.drop_constraint(
        DOCUMENT_CHUNKS_SECTION_ID_FK,
        "document_chunks",
        type_="foreignkey",
    )
    op.create_foreign_key(
        DOCUMENT_CHUNKS_SECTION_ID_FK,
        "document_chunks",
        "filing_sections",
        ["section_id"],
        ["id"],
    )

"""split financial fact fiscal year semantics

Revision ID: 0006_split_fact_fy
Revises: 0005_chunk_embeddings
Create Date: 2026-05-22
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "0006_split_fact_fy"
down_revision: str | Sequence[str] | None = "0005_chunk_embeddings"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_index("ix_financial_facts_fiscal_year", table_name="financial_facts")
    op.alter_column(
        "financial_facts",
        "fiscal_year",
        new_column_name="source_fiscal_year",
        existing_type=sa.Integer(),
        existing_nullable=True,
    )
    op.add_column("financial_facts", sa.Column("fact_fiscal_year", sa.Integer(), nullable=True))
    op.execute(
        "UPDATE financial_facts "
        "SET fact_fiscal_year = source_fiscal_year "
        "WHERE fact_fiscal_year IS NULL"
    )
    op.create_index(
        "ix_financial_facts_source_fiscal_year",
        "financial_facts",
        ["source_fiscal_year"],
    )
    op.create_index(
        "ix_financial_facts_fact_fiscal_year",
        "financial_facts",
        ["fact_fiscal_year"],
    )


def downgrade() -> None:
    op.drop_index("ix_financial_facts_fact_fiscal_year", table_name="financial_facts")
    op.drop_index("ix_financial_facts_source_fiscal_year", table_name="financial_facts")
    op.drop_column("financial_facts", "fact_fiscal_year")
    op.alter_column(
        "financial_facts",
        "source_fiscal_year",
        new_column_name="fiscal_year",
        existing_type=sa.Integer(),
        existing_nullable=True,
    )
    op.create_index("ix_financial_facts_fiscal_year", "financial_facts", ["fiscal_year"])

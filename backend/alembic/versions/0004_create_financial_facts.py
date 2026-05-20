"""create financial facts table

Revision ID: 0004_create_financial_facts
Revises: 0003_create_sec2md_tables
Create Date: 2026-05-16
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "0004_create_financial_facts"
down_revision: str | Sequence[str] | None = "0003_create_sec2md_tables"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "financial_facts",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("company_id", sa.Integer(), nullable=False),
        sa.Column("canonical_metric_key", sa.String(length=64), nullable=False),
        sa.Column("taxonomy_tag", sa.String(length=128), nullable=False),
        sa.Column("label", sa.Text(), nullable=False),
        sa.Column("period_start", sa.Date(), nullable=True),
        sa.Column("period_end", sa.Date(), nullable=False),
        sa.Column("fiscal_year", sa.Integer(), nullable=True),
        sa.Column("fiscal_period", sa.String(length=16), nullable=True),
        sa.Column("form_type", sa.String(length=16), nullable=True),
        sa.Column("filed_date", sa.Date(), nullable=True),
        sa.Column("unit", sa.String(length=32), nullable=False),
        sa.Column("value", sa.Numeric(28, 6), nullable=False),
        sa.Column("source_accession_number", sa.String(length=32), nullable=True),
        sa.Column("source_filing_id", sa.Integer(), nullable=True),
        sa.Column("source_filing_url", sa.Text(), nullable=True),
        sa.Column("source_fact_id", sa.String(length=255), nullable=False),
        sa.Column("is_computed", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("calculation_notes", sa.Text(), nullable=True),
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
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"]),
        sa.ForeignKeyConstraint(["source_filing_id"], ["filings.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_financial_facts_company_id", "financial_facts", ["company_id"])
    op.create_index(
        "ix_financial_facts_canonical_metric_key",
        "financial_facts",
        ["canonical_metric_key"],
    )
    op.create_index("ix_financial_facts_taxonomy_tag", "financial_facts", ["taxonomy_tag"])
    op.create_index("ix_financial_facts_period_end", "financial_facts", ["period_end"])
    op.create_index("ix_financial_facts_fiscal_year", "financial_facts", ["fiscal_year"])
    op.create_index("ix_financial_facts_fiscal_period", "financial_facts", ["fiscal_period"])
    op.create_index("ix_financial_facts_form_type", "financial_facts", ["form_type"])
    op.create_index(
        "ix_financial_facts_source_accession_number",
        "financial_facts",
        ["source_accession_number"],
    )
    op.create_index(
        "ix_financial_facts_source_filing_id",
        "financial_facts",
        ["source_filing_id"],
    )
    op.create_index("ix_financial_facts_source_fact_id", "financial_facts", ["source_fact_id"])


def downgrade() -> None:
    op.drop_index("ix_financial_facts_source_fact_id", table_name="financial_facts")
    op.drop_index("ix_financial_facts_source_filing_id", table_name="financial_facts")
    op.drop_index(
        "ix_financial_facts_source_accession_number",
        table_name="financial_facts",
    )
    op.drop_index("ix_financial_facts_form_type", table_name="financial_facts")
    op.drop_index("ix_financial_facts_fiscal_period", table_name="financial_facts")
    op.drop_index("ix_financial_facts_fiscal_year", table_name="financial_facts")
    op.drop_index("ix_financial_facts_period_end", table_name="financial_facts")
    op.drop_index("ix_financial_facts_taxonomy_tag", table_name="financial_facts")
    op.drop_index("ix_financial_facts_canonical_metric_key", table_name="financial_facts")
    op.drop_index("ix_financial_facts_company_id", table_name="financial_facts")
    op.drop_table("financial_facts")

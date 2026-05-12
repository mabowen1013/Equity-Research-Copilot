"""create SEC ingestion tables

Revision ID: 0002_create_sec_ingestion_tables
Revises: 0001_create_jobs_table
Create Date: 2026-05-12
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "0002_create_sec_ingestion_tables"
down_revision: str | Sequence[str] | None = "0001_create_jobs_table"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "companies",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("ticker", sa.String(length=16), nullable=False),
        sa.Column("cik", sa.String(length=10), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("exchange", sa.String(length=64), nullable=True),
        sa.Column("sic", sa.String(length=16), nullable=True),
        sa.Column("sic_description", sa.String(length=255), nullable=True),
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
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("cik", name="uq_companies_cik"),
        sa.UniqueConstraint("ticker", name="uq_companies_ticker"),
    )
    op.create_index("ix_companies_cik", "companies", ["cik"])
    op.create_index("ix_companies_ticker", "companies", ["ticker"])

    op.create_table(
        "filings",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("company_id", sa.Integer(), nullable=False),
        sa.Column("accession_number", sa.String(length=32), nullable=False),
        sa.Column("form_type", sa.String(length=16), nullable=False),
        sa.Column("filing_date", sa.Date(), nullable=False),
        sa.Column("report_date", sa.Date(), nullable=True),
        sa.Column("primary_document", sa.String(length=255), nullable=True),
        sa.Column("sec_filing_url", sa.Text(), nullable=False),
        sa.Column("sec_primary_document_url", sa.Text(), nullable=True),
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
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("accession_number", name="uq_filings_accession_number"),
    )
    op.create_index("ix_filings_accession_number", "filings", ["accession_number"])
    op.create_index("ix_filings_company_id", "filings", ["company_id"])
    op.create_index("ix_filings_filing_date", "filings", ["filing_date"])
    op.create_index("ix_filings_form_type", "filings", ["form_type"])

    op.create_table(
        "sec_response_cache",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("cache_key", sa.String(length=255), nullable=False),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column(
            "response_json",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("status_code", sa.Integer(), nullable=False),
        sa.Column(
            "fetched_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
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
            "status_code >= 100 AND status_code <= 599",
            name="ck_sec_response_cache_status_code_range",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("cache_key"),
    )
    op.create_index("ix_sec_response_cache_cache_key", "sec_response_cache", ["cache_key"])
    op.create_index("ix_sec_response_cache_expires_at", "sec_response_cache", ["expires_at"])


def downgrade() -> None:
    op.drop_index("ix_sec_response_cache_expires_at", table_name="sec_response_cache")
    op.drop_index("ix_sec_response_cache_cache_key", table_name="sec_response_cache")
    op.drop_table("sec_response_cache")

    op.drop_index("ix_filings_form_type", table_name="filings")
    op.drop_index("ix_filings_filing_date", table_name="filings")
    op.drop_index("ix_filings_company_id", table_name="filings")
    op.drop_index("ix_filings_accession_number", table_name="filings")
    op.drop_table("filings")

    op.drop_index("ix_companies_ticker", table_name="companies")
    op.drop_index("ix_companies_cik", table_name="companies")
    op.drop_table("companies")

"""create research runs audit table

Revision ID: 0007_research_runs
Revises: 0006_split_fact_fy
Create Date: 2026-06-11
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0007_research_runs"
down_revision: str | Sequence[str] | None = "0006_split_fact_fy"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "research_runs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("run_id", sa.String(length=64), nullable=False),
        sa.Column("ticker", sa.String(length=16), nullable=False),
        sa.Column("question", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("validation_status", sa.String(length=32), nullable=False),
        sa.Column("duration_ms", sa.Float(), nullable=True),
        sa.Column(
            "payload",
            JSONB,
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index(
        "ix_research_runs_run_id",
        "research_runs",
        ["run_id"],
        unique=True,
    )
    op.create_index("ix_research_runs_ticker", "research_runs", ["ticker"])
    op.create_index("ix_research_runs_status", "research_runs", ["status"])


def downgrade() -> None:
    op.drop_index("ix_research_runs_status", table_name="research_runs")
    op.drop_index("ix_research_runs_ticker", table_name="research_runs")
    op.drop_index("ix_research_runs_run_id", table_name="research_runs")
    op.drop_table("research_runs")

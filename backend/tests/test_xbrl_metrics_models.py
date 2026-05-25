from app.db.base import Base
from app.models import FinancialFact
from app.schemas import FinancialFactRead


def test_financial_facts_table_contains_metric_and_source_columns() -> None:
    columns = FinancialFact.__table__.columns

    assert set(columns.keys()) == {
        "id",
        "company_id",
        "canonical_metric_key",
        "taxonomy_tag",
        "label",
        "period_start",
        "period_end",
        "source_fiscal_year",
        "fact_fiscal_year",
        "fiscal_period",
        "form_type",
        "filed_date",
        "unit",
        "value",
        "source_accession_number",
        "source_filing_id",
        "source_filing_url",
        "source_fact_id",
        "is_computed",
        "calculation_notes",
        "created_at",
        "updated_at",
    }


def test_financial_facts_table_is_registered_for_migrations() -> None:
    assert "financial_facts" in Base.metadata.tables


def test_financial_fact_schema_allows_orm_serialization() -> None:
    assert FinancialFactRead.model_config["from_attributes"] is True

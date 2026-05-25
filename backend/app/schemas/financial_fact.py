from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, field_serializer


class FinancialFactRead(BaseModel):
    id: int
    company_id: int
    canonical_metric_key: str
    taxonomy_tag: str
    label: str
    period_start: date | None
    period_end: date
    source_fiscal_year: int | None
    fact_fiscal_year: int | None
    fiscal_period: str | None
    form_type: str | None
    filed_date: date | None
    unit: str
    value: Decimal
    source_accession_number: str | None
    source_filing_id: int | None
    source_filing_url: str | None
    source_fact_id: str
    is_computed: bool
    calculation_notes: str | None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)

    @field_serializer("value")
    def serialize_value(self, value: Decimal) -> str:
        return format(value, "f")

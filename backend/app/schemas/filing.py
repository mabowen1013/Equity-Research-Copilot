from datetime import date, datetime

from pydantic import BaseModel, ConfigDict


class FilingRead(BaseModel):
    id: int
    company_id: int
    accession_number: str
    form_type: str
    filing_date: date
    report_date: date | None
    primary_document: str | None
    sec_filing_url: str
    sec_primary_document_url: str | None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)

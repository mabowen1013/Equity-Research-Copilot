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


class FilingDocumentRead(BaseModel):
    id: int
    filing_id: int
    source_url: str
    cache_path: str | None
    content_sha256: str | None
    content_type: str | None
    byte_size: int | None
    status: str
    parser_version: str | None
    error_message: str | None
    downloaded_at: datetime | None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class FilingSectionRead(BaseModel):
    id: int
    filing_id: int
    section_key: str
    section_title: str
    section_order: int
    normalized_text: str
    start_offset: int
    end_offset: int
    extraction_confidence: int
    extraction_method: str
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class DocumentChunkRead(BaseModel):
    id: int
    filing_id: int
    section_id: int
    chunk_index: int
    chunk_text: str
    token_count: int
    start_offset: int
    end_offset: int
    text_hash: str
    accession_number: str
    form_type: str
    filing_date: date
    section_key: str
    sec_url: str
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)

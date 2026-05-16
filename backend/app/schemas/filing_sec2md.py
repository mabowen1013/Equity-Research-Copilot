from datetime import date, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict


class FilingDocumentRead(BaseModel):
    id: int
    filing_id: int
    source_url: str
    content_sha256: str
    parser_name: str
    parser_version: str
    fetched_at: datetime
    parsed_at: datetime | None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class FilingSectionSummary(BaseModel):
    id: int
    filing_id: int
    section_key: str
    part: str | None
    item: str | None
    title: str | None
    section_order: int
    start_page: int | None
    end_page: int | None
    start_display_page: int | None
    end_display_page: int | None
    token_count: int
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class FilingSectionRead(FilingSectionSummary):
    markdown_text: str


class DocumentChunkRead(BaseModel):
    id: int
    filing_id: int
    section_id: int
    chunk_index: int
    chunk_text: str
    token_count: int
    accession_number: str
    form_type: str
    filing_date: date
    section_label: str
    sec_url: str
    start_page: int | None
    end_page: int | None
    start_display_page: int | None
    end_display_page: int | None
    element_ids: list[str]
    xbrl_tags: list[str]
    source_start_offset: int | None
    source_end_offset: int | None
    has_table: bool
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class FilingParseSummary(BaseModel):
    filing_document: FilingDocumentRead
    sections_count: int
    chunks_count: int
    section_keys: list[str]
    parser_warnings: list[str] = []
    metadata: dict[str, Any] = {}

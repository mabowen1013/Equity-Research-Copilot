from app.db.base import Base
from app.models import DocumentChunk, FilingDocument, FilingSection
from app.schemas import DocumentChunkRead, FilingDocumentRead, FilingSectionRead, FilingSectionSummary


def test_filing_document_table_contains_raw_and_parser_metadata_columns() -> None:
    columns = FilingDocument.__table__.columns

    assert set(columns.keys()) == {
        "id",
        "filing_id",
        "raw_html",
        "annotated_html",
        "source_url",
        "content_sha256",
        "parser_name",
        "parser_version",
        "fetched_at",
        "parsed_at",
        "created_at",
        "updated_at",
    }


def test_filing_sections_table_contains_section_metadata_columns() -> None:
    columns = FilingSection.__table__.columns

    assert set(columns.keys()) == {
        "id",
        "filing_id",
        "section_key",
        "part",
        "item",
        "title",
        "section_order",
        "start_page",
        "end_page",
        "start_display_page",
        "end_display_page",
        "markdown_text",
        "token_count",
        "created_at",
        "updated_at",
    }


def test_document_chunks_table_contains_citation_metadata_columns() -> None:
    columns = DocumentChunk.__table__.columns

    assert set(columns.keys()) == {
        "id",
        "filing_id",
        "section_id",
        "chunk_index",
        "chunk_text",
        "token_count",
        "accession_number",
        "form_type",
        "filing_date",
        "section_label",
        "sec_url",
        "start_page",
        "end_page",
        "start_display_page",
        "end_display_page",
        "element_ids",
        "xbrl_tags",
        "source_start_offset",
        "source_end_offset",
        "has_table",
        "created_at",
        "updated_at",
    }


def test_sec2md_tables_are_registered_for_migrations() -> None:
    assert "filing_documents" in Base.metadata.tables
    assert "filing_sections" in Base.metadata.tables
    assert "document_chunks" in Base.metadata.tables


def test_sec2md_schemas_allow_orm_serialization() -> None:
    assert FilingDocumentRead.model_config["from_attributes"] is True
    assert FilingSectionSummary.model_config["from_attributes"] is True
    assert FilingSectionRead.model_config["from_attributes"] is True
    assert DocumentChunkRead.model_config["from_attributes"] is True

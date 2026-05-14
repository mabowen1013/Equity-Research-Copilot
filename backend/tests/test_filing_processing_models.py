from app.db.base import Base
from app.models import DocumentChunk, FilingDocument, FilingSection


def test_filing_documents_table_contains_raw_document_metadata_columns() -> None:
    columns = FilingDocument.__table__.columns

    assert set(columns.keys()) == {
        "id",
        "filing_id",
        "source_url",
        "cache_path",
        "content_sha256",
        "content_type",
        "byte_size",
        "status",
        "parser_version",
        "error_message",
        "downloaded_at",
        "created_at",
        "updated_at",
    }


def test_filing_documents_table_links_to_filings_and_tracks_status() -> None:
    foreign_keys = {
        foreign_key.target_fullname
        for foreign_key in FilingDocument.filing_id.foreign_keys
    }
    constraint_names = {
        constraint.name
        for constraint in FilingDocument.__table__.constraints
        if constraint.name is not None
    }

    assert foreign_keys == {"filings.id"}
    assert "uq_filing_documents_filing_id" in constraint_names
    assert "ck_filing_documents_status" in constraint_names
    assert "ck_filing_documents_byte_size_nonnegative" in constraint_names


def test_filing_sections_table_contains_section_metadata_columns() -> None:
    columns = FilingSection.__table__.columns

    assert set(columns.keys()) == {
        "id",
        "filing_id",
        "section_key",
        "section_title",
        "section_order",
        "normalized_text",
        "start_offset",
        "end_offset",
        "extraction_confidence",
        "extraction_method",
        "created_at",
        "updated_at",
    }


def test_filing_sections_table_links_to_filings_and_deduplicates_sections() -> None:
    foreign_keys = {
        foreign_key.target_fullname
        for foreign_key in FilingSection.filing_id.foreign_keys
    }
    constraint_names = {
        constraint.name
        for constraint in FilingSection.__table__.constraints
        if constraint.name is not None
    }

    assert foreign_keys == {"filings.id"}
    assert "uq_filing_sections_key" in constraint_names
    assert "uq_filing_sections_order" in constraint_names
    assert "ck_filing_sections_order_nonnegative" in constraint_names
    assert "ck_filing_sections_offsets" in constraint_names
    assert "ck_filing_sections_confidence_range" in constraint_names


def test_document_chunks_table_contains_citation_metadata_columns() -> None:
    columns = DocumentChunk.__table__.columns

    assert set(columns.keys()) == {
        "id",
        "filing_id",
        "section_id",
        "chunk_index",
        "chunk_text",
        "token_count",
        "start_offset",
        "end_offset",
        "text_hash",
        "accession_number",
        "form_type",
        "filing_date",
        "section_key",
        "sec_url",
        "created_at",
        "updated_at",
    }


def test_document_chunks_table_links_to_filings_and_sections() -> None:
    filing_foreign_keys = {
        foreign_key.target_fullname
        for foreign_key in DocumentChunk.filing_id.foreign_keys
    }
    section_foreign_keys = {
        foreign_key.target_fullname
        for foreign_key in DocumentChunk.section_id.foreign_keys
    }
    constraint_names = {
        constraint.name
        for constraint in DocumentChunk.__table__.constraints
        if constraint.name is not None
    }

    assert filing_foreign_keys == {"filings.id"}
    assert section_foreign_keys == {"filing_sections.id"}
    assert "uq_document_chunks_section_index" in constraint_names
    assert "ck_document_chunks_index_nonnegative" in constraint_names
    assert "ck_document_chunks_token_count_positive" in constraint_names
    assert "ck_document_chunks_offsets" in constraint_names


def test_filing_processing_tables_are_registered_for_migrations() -> None:
    assert "filing_documents" in Base.metadata.tables
    assert "filing_sections" in Base.metadata.tables
    assert "document_chunks" in Base.metadata.tables

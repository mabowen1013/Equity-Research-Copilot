from __future__ import annotations

from datetime import UTC, date, datetime

from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import Session, sessionmaker

from app.models import DocumentChunk, Filing, FilingDocument, FilingSection
from app.services.filing_sec2md import (
    SEC2MD_EXTRACTION_METHOD,
    SEC2MD_PARSER_VERSION,
    Sec2MDFilingProcessingService,
    parse_sec2md_filing,
)

NOW = datetime(2026, 5, 16, 12, 0, tzinfo=UTC)

FILING_HTML = """
<html>
  <body>
    <div>PART I</div>
    <div>Item 1. Business</div>
    <p>We sell products and services.</p>
    <div>Item 1A. Risk Factors</div>
    <p>Risks include supply constraints and demand volatility.</p>
    <table>
      <tr><th>Metric</th><th>2026</th></tr>
      <tr>
        <td>Revenue</td>
        <td><ix:nonfraction name="us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax">100</ix:nonfraction></td>
      </tr>
    </table>
  </body>
</html>
"""


def make_session() -> Session:
    engine = create_engine("sqlite:///:memory:")

    @event.listens_for(engine, "connect")
    def register_now_function(dbapi_connection, connection_record) -> None:
        dbapi_connection.create_function("now", 0, lambda: "2026-05-16 00:00:00")

    Filing.__table__.create(engine)
    FilingDocument.__table__.create(engine)
    FilingSection.__table__.create(engine)
    DocumentChunk.__table__.create(engine)
    return sessionmaker(bind=engine)()


def make_filing(db_session: Session) -> Filing:
    filing = Filing(
        id=7,
        company_id=42,
        accession_number="0000320193-26-000013",
        form_type="10-K",
        filing_date=date(2026, 5, 1),
        report_date=date(2026, 3, 28),
        primary_document="example.htm",
        sec_filing_url="https://www.sec.gov/Archives/example-index.htm",
        sec_primary_document_url="https://www.sec.gov/Archives/example.htm",
        created_at=NOW,
        updated_at=NOW,
    )
    db_session.add(filing)
    db_session.flush()
    return filing


def make_document(db_session: Session, filing: Filing, cache_path: str) -> FilingDocument:
    document = FilingDocument(
        id=99,
        filing_id=filing.id,
        source_url="https://www.sec.gov/Archives/example.htm",
        cache_path=cache_path,
        content_sha256="a" * 64,
        content_type="text/html",
        byte_size=100,
        status="downloaded",
        created_at=NOW,
        updated_at=NOW,
    )
    db_session.add(document)
    db_session.flush()
    return document


def test_parse_sec2md_filing_extracts_sections_and_markdown_tables() -> None:
    parsed = parse_sec2md_filing(FILING_HTML, "10-K")

    assert parsed.used_full_document_fallback is False
    assert [section.section_key for section in parsed.sections] == ["item_1", "item_1a"]
    assert parsed.sections[0].section_title == "Part I Item 1. Business"
    assert "We sell products and services." in parsed.sections[0].markdown
    assert "| Metric | 2026 |" in parsed.sections[1].markdown
    assert "us-gaap" not in parsed.sections[1].markdown


def test_sec2md_processing_stores_sections_chunks_and_metadata(tmp_path) -> None:
    db_session = make_session()
    filing = make_filing(db_session)
    cache_path = tmp_path / "filing.htm"
    cache_path.write_text(FILING_HTML)
    document = make_document(db_session, filing, str(cache_path))

    result = Sec2MDFilingProcessingService(
        db_session,
        chunk_size=120,
        chunk_overlap=0,
        clock=lambda: NOW,
    ).process_filing_document(filing, document)

    stored_sections = db_session.scalars(
        select(FilingSection).where(FilingSection.filing_id == filing.id),
    ).all()
    stored_chunks = db_session.scalars(
        select(DocumentChunk).where(DocumentChunk.filing_id == filing.id),
    ).all()

    assert document.parser_version == SEC2MD_PARSER_VERSION
    assert [section.section_key for section in stored_sections] == ["item_1", "item_1a"]
    assert all(section.extraction_method == SEC2MD_EXTRACTION_METHOD for section in stored_sections)
    assert stored_sections[0].page_start == 1
    assert len(result.sections) == 2
    assert len(stored_chunks) == 2
    assert len(result.chunks) == 2
    assert stored_chunks[0].chunk_text.startswith("Item 1. Business")
    assert "Item 1A. Risk Factors" not in stored_chunks[0].chunk_text
    assert stored_chunks[1].has_table is True
    assert stored_chunks[1].page_start == 1
    assert stored_chunks[1].section_key == "item_1a"
    assert result.metrics.as_payload()["total_sections"] == 2
    assert result.metrics.as_payload()["chunks_with_tables"] == 1

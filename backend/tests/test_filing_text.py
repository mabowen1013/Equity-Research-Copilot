from __future__ import annotations

from datetime import UTC, date, datetime

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from app.models import Filing, FilingDocument, FilingSection
from app.services import (
    FILING_TEXT_PARSER_VERSION,
    FULL_DOCUMENT_EXTRACTION_METHOD,
    FULL_DOCUMENT_SECTION_KEY,
    FilingTextExtractionError,
    FilingTextExtractionService,
    extract_normalized_text,
    normalize_extracted_text,
)

NOW = datetime(2026, 5, 14, 12, 0, tzinfo=UTC)


@pytest.fixture
def db_session() -> Session:
    engine = create_engine("sqlite:///:memory:")

    @event.listens_for(engine, "connect")
    def register_now_function(dbapi_connection, connection_record) -> None:
        dbapi_connection.create_function("now", 0, lambda: "2026-05-14 00:00:00")

    Filing.__table__.create(engine)
    FilingDocument.__table__.create(engine)
    FilingSection.__table__.create(engine)
    SessionLocal = sessionmaker(bind=engine)

    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


def make_filing(db_session: Session) -> Filing:
    filing = Filing(
        id=7,
        company_id=42,
        accession_number="0000320193-24-000123",
        form_type="10-K",
        filing_date=date(2024, 11, 1),
        report_date=date(2024, 9, 28),
        primary_document="aapl-20240928.htm",
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


def test_extract_normalized_text_removes_non_content_nodes_and_preserves_layout() -> None:
    html = """
    <html>
      <head>
        <style>.hidden { display: none; }</style>
        <script>removeMe()</script>
      </head>
      <body>
        <h1>Item 1A. Risk Factors</h1>
        <p>Apple faces supply constraints.</p>
        <p hidden>This hidden paragraph should not appear.</p>
        <table>
          <tr><th>Metric</th><th>Value</th></tr>
          <tr><td>Revenue</td><td>100</td></tr>
        </table>
      </body>
    </html>
    """

    text = extract_normalized_text(html)

    assert "Item 1A. Risk Factors" in text
    assert "Apple faces supply constraints." in text
    assert "Metric" in text
    assert "Value" in text
    assert "Revenue  100" in text
    assert "removeMe" not in text
    assert "hidden paragraph" not in text


def test_normalize_extracted_text_collapses_excessive_blank_lines() -> None:
    assert normalize_extracted_text(" Alpha  \n\n\n\nBeta\r\n") == "Alpha\n\nBeta"


def test_extract_full_document_section_stores_fallback_section(
    db_session: Session,
    tmp_path,
) -> None:
    filing = make_filing(db_session)
    cache_path = tmp_path / "filing.htm"
    cache_path.write_text(
        """
        <html>
          <body>
            <h1>Apple Inc.</h1>
            <p>Management's discussion appears here.</p>
          </body>
        </html>
        """,
        encoding="utf-8",
    )
    document = make_document(db_session, filing, str(cache_path))

    section = FilingTextExtractionService(
        db_session,
        clock=lambda: NOW,
    ).extract_full_document_section(filing, document)

    assert section.filing_id == filing.id
    assert section.section_key == FULL_DOCUMENT_SECTION_KEY
    assert section.section_title == "Full document"
    assert section.section_order == 0
    assert "Apple Inc." in section.normalized_text
    assert "Management's discussion appears here." in section.normalized_text
    assert section.start_offset == 0
    assert section.end_offset == len(section.normalized_text)
    assert section.extraction_confidence == 50
    assert section.extraction_method == FULL_DOCUMENT_EXTRACTION_METHOD
    assert document.parser_version == FILING_TEXT_PARSER_VERSION
    assert document.updated_at == NOW


def test_extract_full_document_section_updates_existing_fallback_section(
    db_session: Session,
    tmp_path,
) -> None:
    filing = make_filing(db_session)
    existing_section = FilingSection(
        id=123,
        filing_id=filing.id,
        section_key=FULL_DOCUMENT_SECTION_KEY,
        section_title="Old title",
        section_order=0,
        normalized_text="old",
        start_offset=0,
        end_offset=3,
        extraction_confidence=1,
        extraction_method="old",
        created_at=NOW,
        updated_at=NOW,
    )
    db_session.add(existing_section)
    cache_path = tmp_path / "filing.htm"
    cache_path.write_text("<html><body><p>Updated filing text.</p></body></html>", encoding="utf-8")
    document = make_document(db_session, filing, str(cache_path))

    section = FilingTextExtractionService(db_session).extract_full_document_section(
        filing,
        document,
    )

    assert section.id == 123
    assert section.normalized_text == "Updated filing text."
    assert db_session.query(FilingSection).count() == 1


def test_extract_full_document_section_requires_downloaded_document(
    db_session: Session,
) -> None:
    filing = make_filing(db_session)
    document = FilingDocument(
        id=99,
        filing_id=filing.id,
        source_url="https://www.sec.gov/Archives/example.htm",
        status="failed",
        created_at=NOW,
        updated_at=NOW,
    )

    with pytest.raises(FilingTextExtractionError, match="must be downloaded"):
        FilingTextExtractionService(db_session).extract_full_document_section(
            filing,
            document,
        )

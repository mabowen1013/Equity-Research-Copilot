from __future__ import annotations

from datetime import UTC, date, datetime

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from app.models import Filing, FilingDocument, FilingSection
from app.services import (
    ExtractedFilingSection,
    FILING_TEXT_PARSER_VERSION,
    FULL_DOCUMENT_EXTRACTION_METHOD,
    FULL_DOCUMENT_SECTION_KEY,
    FilingTextExtractionError,
    FilingTextExtractionService,
    REGEX_EXTRACTION_METHOD,
    SEC_PARSER_VALIDATED_REGEX_OFFSETS_METHOD,
    build_extraction_metrics,
    extract_normalized_text,
    extract_sec_parser_sections,
    extract_structured_sections,
    normalize_extracted_text,
    should_use_sec_parser_primary,
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


def assert_sections_do_not_overlap(sections) -> None:
    for section in sections:
        assert section.start_offset <= section.end_offset

    for previous, current in zip(sections, sections[1:]):
        assert previous.end_offset <= current.start_offset


def assert_section_order_follows_start_offset(sections: list[FilingSection]) -> None:
    assert [section.section_order for section in sections] == list(range(len(sections)))
    assert [section.start_offset for section in sections] == sorted(
        section.start_offset
        for section in sections
    )


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
        <template>Template-only text should not appear.</template>
        <p style="DISPLAY : none">Display-hidden text should not appear.</p>
        <p style="visibility:
          hidden !important">Visibility-hidden text should not appear.</p>
        <p style="color: red">Visible styled text should remain.</p>
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
    assert "Visible styled text should remain." in text
    assert "removeMe" not in text
    assert "hidden paragraph" not in text
    assert "Template-only text" not in text
    assert "Display-hidden text" not in text
    assert "Visibility-hidden text" not in text


def test_normalize_extracted_text_collapses_excessive_blank_lines() -> None:
    assert normalize_extracted_text(" Alpha  \n\n\n\nBeta\r\n") == "Alpha\n\nBeta"


def test_build_extraction_metrics_counts_parser_outcomes() -> None:
    sections = [
        ExtractedFilingSection(
            section_key="part_i_item_2",
            section_title="Part I Item 2.",
            normalized_text="text",
            start_offset=0,
            end_offset=4,
            extraction_confidence=90,
            extraction_method=SEC_PARSER_VALIDATED_REGEX_OFFSETS_METHOD,
        ),
        ExtractedFilingSection(
            section_key="item_7",
            section_title="Item 7.",
            normalized_text="text",
            start_offset=5,
            end_offset=9,
            extraction_confidence=80,
            extraction_method=REGEX_EXTRACTION_METHOD,
        ),
        ExtractedFilingSection(
            section_key=FULL_DOCUMENT_SECTION_KEY,
            section_title="Full document",
            normalized_text="text",
            start_offset=0,
            end_offset=4,
            extraction_confidence=50,
            extraction_method=FULL_DOCUMENT_EXTRACTION_METHOD,
        ),
    ]

    metrics = build_extraction_metrics(sections)

    assert metrics.as_payload() == {
        "total_sections": 3,
        "sec_parser_validated_regex_offsets_count": 1,
        "regex_fallback_count": 1,
        "full_document_fallback_count": 1,
    }


def test_extract_filing_sections_stores_fallback_section(
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

    service = FilingTextExtractionService(
        db_session,
        clock=lambda: NOW,
    )
    sections = service.extract_filing_sections(filing, document)
    section = sections[0]

    assert len(sections) == 1
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
    assert service.last_extraction_metrics is not None
    assert service.last_extraction_metrics.as_payload() == {
        "total_sections": 1,
        "sec_parser_validated_regex_offsets_count": 0,
        "regex_fallback_count": 0,
        "full_document_fallback_count": 1,
    }


def test_extract_filing_sections_stores_10k_item_sections(
    db_session: Session,
    tmp_path,
) -> None:
    filing = make_filing(db_session)
    cache_path = tmp_path / "aapl-10k.htm"
    cache_path.write_text(
        """
        <html>
          <body>
            <p>Table of Contents</p>
            <p>Item 1. Business 4</p>
            <p>Item 1A. Risk Factors 8</p>
            <p>Item 7. Management's Discussion and Analysis 22</p>
            <h1>Item 1. Business</h1>
            <p>Apple designs, manufactures, and markets smartphones and services.</p>
            <h1>Item 1A. Risk Factors</h1>
            <p>Risks include supply constraints, competition, and macroeconomic conditions.</p>
            <h1>Item 7. Management's Discussion and Analysis</h1>
            <p>Management discusses revenue, gross margin, and operating expenses.</p>
          </body>
        </html>
        """,
        encoding="utf-8",
    )
    document = make_document(db_session, filing, str(cache_path))

    sections = FilingTextExtractionService(
        db_session,
        clock=lambda: NOW,
    ).extract_filing_sections(filing, document)

    assert [section.section_key for section in sections] == ["item_1", "item_1a", "item_7"]
    assert [section.section_order for section in sections] == [0, 1, 2]
    assert_sections_do_not_overlap(sections)
    assert_section_order_follows_start_offset(sections)
    assert "Apple designs" in sections[0].normalized_text
    assert "Risks include supply constraints" in sections[1].normalized_text
    assert "Management discusses revenue" in sections[2].normalized_text
    assert {section.extraction_method for section in sections} == {REGEX_EXTRACTION_METHOD}
    assert document.parser_version == FILING_TEXT_PARSER_VERSION


def test_extract_filing_sections_stores_10q_part_item_sections(
    db_session: Session,
    tmp_path,
) -> None:
    filing = make_filing(db_session)
    filing.form_type = "10-Q"
    cache_path = tmp_path / "tsla-10q.htm"
    cache_path.write_text(
        """
        <html>
          <body>
            <h1>PART I - FINANCIAL INFORMATION</h1>
            <h2>Item 2. Management's Discussion and Analysis of Financial Condition</h2>
            <p>Tesla discusses deliveries, revenue, and operating costs.</p>
            <h1>PART II - OTHER INFORMATION</h1>
            <h2>Item 1A. Risk Factors</h2>
            <p>Risk factors did not materially change from the annual report.</p>
          </body>
        </html>
        """,
        encoding="utf-8",
    )
    document = make_document(db_session, filing, str(cache_path))

    service = FilingTextExtractionService(db_session)
    sections = service.extract_filing_sections(
        filing,
        document,
    )

    assert [section.section_key for section in sections] == [
        "part_i_item_2",
        "part_ii_item_1a",
    ]
    assert_sections_do_not_overlap(sections)
    assert_section_order_follows_start_offset(sections)
    assert sections[0].section_title.startswith("Part I Item 2.")
    assert "Tesla discusses deliveries" in sections[0].normalized_text
    assert "Risk factors did not materially change" in sections[1].normalized_text
    assert service.last_extraction_metrics is not None
    assert service.last_extraction_metrics.sec_parser_validated_regex_offsets_count == 2
    assert service.last_extraction_metrics.total_sections == 2


def test_extract_filing_sections_distinguishes_same_10q_item_across_parts(
    db_session: Session,
    tmp_path,
) -> None:
    filing = make_filing(db_session)
    filing.form_type = "10-Q"
    cache_path = tmp_path / "same-item-10q.htm"
    cache_path.write_text(
        """
        <html>
          <body>
            <h1>PART I - FINANCIAL INFORMATION</h1>
            <h2>Item 1. Financial Statements</h2>
            <p>Condensed consolidated balance sheets appear here.</p>
            <h1>PART II - OTHER INFORMATION</h1>
            <h2>Item 1. Legal Proceedings</h2>
            <p>The company describes legal proceedings here.</p>
          </body>
        </html>
        """,
        encoding="utf-8",
    )
    document = make_document(db_session, filing, str(cache_path))

    sections = FilingTextExtractionService(db_session).extract_filing_sections(
        filing,
        document,
    )

    assert [section.section_key for section in sections] == [
        "part_i_item_1",
        "part_ii_item_1",
    ]
    assert [section.section_title for section in sections] == [
        "Part I Item 1. Financial Statements",
        "Part II Item 1. Legal Proceedings",
    ]
    assert_sections_do_not_overlap(sections)
    assert_section_order_follows_start_offset(sections)
    assert "Condensed consolidated balance sheets" in sections[0].normalized_text
    assert "legal proceedings" in sections[1].normalized_text


def test_extract_filing_sections_stores_8k_item_sections(
    db_session: Session,
    tmp_path,
) -> None:
    filing = make_filing(db_session)
    filing.form_type = "8-K"
    cache_path = tmp_path / "nvda-8k.htm"
    cache_path.write_text(
        """
        <html>
          <body>
            <h1>Item 2.02 Results of Operations and Financial Condition.</h1>
            <p>NVIDIA reported quarterly revenue and gross margin.</p>
            <h1>Item 9.01 Financial Statements and Exhibits.</h1>
            <p>The exhibit list includes the earnings release.</p>
          </body>
        </html>
        """,
        encoding="utf-8",
    )
    document = make_document(db_session, filing, str(cache_path))

    sections = FilingTextExtractionService(db_session).extract_filing_sections(
        filing,
        document,
    )

    assert [section.section_key for section in sections] == ["item_2_02", "item_9_01"]
    assert "NVIDIA reported quarterly revenue" in sections[0].normalized_text
    assert "earnings release" in sections[1].normalized_text
    assert {section.extraction_method for section in sections} == {REGEX_EXTRACTION_METHOD}


def test_sec_parser_primary_is_currently_limited_to_10q() -> None:
    assert should_use_sec_parser_primary("10-Q") is True
    assert should_use_sec_parser_primary("10-K") is False
    assert should_use_sec_parser_primary("8-K") is False


def test_extract_sec_parser_sections_skips_non_10q_forms() -> None:
    html = """
    <html>
      <body>
        <h1>Item 1A. Risk Factors</h1>
        <p>Risk text.</p>
      </body>
    </html>
    """

    assert extract_sec_parser_sections(html, "10-K") == []
    assert extract_sec_parser_sections(html, "8-K") == []


def test_extract_structured_sections_uses_regex_fallback_without_sec_parser_titles() -> None:
    sections = extract_structured_sections(
        """
Item 1A. Risk Factors
Risk factor text.

Item 7. Management's Discussion and Analysis
MD&A text.
        """,
        "10-K",
    )

    assert [section.section_key for section in sections] == ["item_1a", "item_7"]
    assert {section.extraction_method for section in sections} == {REGEX_EXTRACTION_METHOD}


def test_extract_structured_sections_handles_split_item_heading_title_lines() -> None:
    normalized_text = normalize_extracted_text(
        """
Item 2.
Properties
Apple owns and leases facilities for operations.

Item 1A.
Risk Factors
Risks include supply constraints and competition.

Item 7.
Management's Discussion and Analysis
Management discusses revenue and margins.
        """,
    )

    sections = extract_structured_sections(normalized_text, "10-K")

    assert [section.section_key for section in sections] == ["item_2", "item_1a", "item_7"]
    assert [section.section_title for section in sections] == [
        "Item 2. Properties",
        "Item 1A. Risk Factors",
        "Item 7. Management's Discussion and Analysis",
    ]
    assert_sections_do_not_overlap(sections)
    assert sections[0].start_offset == normalized_text.index("Item 2.")
    assert sections[0].normalized_text.startswith("Item 2.\nProperties")
    assert "Apple owns and leases facilities" in sections[0].normalized_text
    assert "Risks include supply constraints" in sections[1].normalized_text
    assert "Management discusses revenue" in sections[2].normalized_text
    assert {section.extraction_method for section in sections} == {REGEX_EXTRACTION_METHOD}


def test_extract_structured_sections_ignores_item_only_line_without_title() -> None:
    sections = extract_structured_sections(
        """
Item 1.

Item 1A. Risk Factors
Risk factor text.
        """,
        "10-K",
    )

    assert [section.section_key for section in sections] == ["item_1a"]


def test_extract_sec_parser_sections_reads_nested_10q_semantic_tree() -> None:
    html = """
    <html>
      <body>
        <h1>PART I - FINANCIAL INFORMATION</h1>
        <h2>Item 2. Management's Discussion and Analysis</h2>
        <p>Tesla discusses deliveries, revenue, and operating costs.</p>
        <h1>PART II - OTHER INFORMATION</h1>
        <h2>Item 1A. Risk Factors</h2>
        <p>Risk factors did not materially change.</p>
      </body>
    </html>
    """

    sections = extract_sec_parser_sections(html, "10-Q")

    assert [section.section_key for section in sections] == [
        "part_i_item_2",
        "part_ii_item_1a",
    ]
    assert "Tesla discusses deliveries" in sections[0].section_text
    assert "Risk factors did not materially change" in sections[1].section_text


def test_extract_structured_sections_uses_sec_parser_validated_regex_offsets() -> None:
    html = """
    <html>
      <body>
        <h1>PART I - FINANCIAL INFORMATION</h1>
        <h2>Item 2. Management's Discussion and Analysis</h2>
        <p>Tesla discusses deliveries, revenue, and operating costs.</p>
        <h1>PART II - OTHER INFORMATION</h1>
        <h2>Item 1A. Risk Factors</h2>
        <p>Risk factors did not materially change.</p>
      </body>
    </html>
    """
    normalized_text = extract_normalized_text(html)

    sections = extract_structured_sections(normalized_text, "10-Q", cleaned_html=html)

    assert [section.section_key for section in sections] == [
        "part_i_item_2",
        "part_ii_item_1a",
    ]
    assert {section.extraction_method for section in sections} == {
        SEC_PARSER_VALIDATED_REGEX_OFFSETS_METHOD,
    }
    assert {section.extraction_confidence for section in sections} == {90}


def test_extract_structured_sections_keeps_unvalidated_regex_sections() -> None:
    normalized_text = normalize_extracted_text(
        """
PART I - FINANCIAL INFORMATION
Item 2. Management's Discussion and Analysis
Tesla discusses deliveries, revenue, and operating costs.

PART II - OTHER INFORMATION
Item 1A. Risk Factors
Risk factors did not materially change.
        """,
    )
    cleaned_html = """
    <html>
      <body>
        <h1>PART I - FINANCIAL INFORMATION</h1>
        <h2>Item 2. Management's Discussion and Analysis</h2>
        <p>Tesla discusses deliveries, revenue, and operating costs.</p>
      </body>
    </html>
    """

    sections = extract_structured_sections(normalized_text, "10-Q", cleaned_html=cleaned_html)

    assert [section.section_key for section in sections] == [
        "part_i_item_2",
        "part_ii_item_1a",
    ]
    assert sections[0].extraction_method == SEC_PARSER_VALIDATED_REGEX_OFFSETS_METHOD
    assert sections[0].section_title == "Part I Item 2. Management's Discussion and Analysis"
    assert sections[1].extraction_method == REGEX_EXTRACTION_METHOD


def test_extract_filing_sections_updates_existing_fallback_section(
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

    sections = FilingTextExtractionService(db_session).extract_filing_sections(
        filing,
        document,
    )

    section = sections[0]
    assert len(sections) == 1
    assert section.id == 123
    assert section.normalized_text == "Updated filing text."
    assert db_session.query(FilingSection).count() == 1


def test_extract_filing_sections_requires_downloaded_document(
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
        FilingTextExtractionService(db_session).extract_filing_sections(
            filing,
            document,
        )

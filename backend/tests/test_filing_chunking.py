from __future__ import annotations

import hashlib
from datetime import UTC, date, datetime

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from app.models import DocumentChunk, Filing, FilingSection
from app.services import (
    FilingChunkingService,
    build_text_chunk_candidates,
    split_text_blocks,
)

NOW = datetime(2026, 5, 15, 12, 0, tzinfo=UTC)


def count_words(text: str) -> int:
    return len(text.split())


def make_session() -> Session:
    engine = create_engine("sqlite:///:memory:")
    Filing.__table__.create(engine)
    FilingSection.__table__.create(engine)
    DocumentChunk.__table__.create(engine)
    return sessionmaker(bind=engine)()


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


def make_section(
    db_session: Session,
    filing: Filing,
    *,
    section_key: str,
    section_order: int,
    normalized_text: str,
    start_offset: int,
) -> FilingSection:
    section = FilingSection(
        filing_id=filing.id,
        section_key=section_key,
        section_title=section_key,
        section_order=section_order,
        normalized_text=normalized_text,
        start_offset=start_offset,
        end_offset=start_offset + len(normalized_text),
        extraction_confidence=90,
        extraction_method="test",
        created_at=NOW,
        updated_at=NOW,
    )
    db_session.add(section)
    db_session.flush()
    return section


def test_split_text_blocks_uses_blank_lines_as_paragraph_boundaries() -> None:
    text = "First paragraph.\ncontinues here.\n\nSecond paragraph.\n\n  Third paragraph."

    blocks = split_text_blocks(text)

    assert [block.text for block in blocks] == [
        "First paragraph.\ncontinues here.",
        "Second paragraph.",
        "Third paragraph.",
    ]
    assert [text[block.start_offset : block.end_offset] for block in blocks] == [
        "First paragraph.\ncontinues here.",
        "Second paragraph.",
        "Third paragraph.",
    ]


def test_build_text_chunk_candidates_preserves_source_offsets() -> None:
    text = (
        "Intro sentence.\n\n"
        "Revenue increased because services grew and product sales improved.\n\n"
        "Margins changed with mix."
    )

    chunks = build_text_chunk_candidates(
        text,
        target_tokens=4,
        max_tokens=9,
        token_counter=count_words,
    )

    assert len(chunks) == 3
    for chunk in chunks:
        assert text[chunk.start_offset : chunk.end_offset] == chunk.chunk_text
        assert chunk.token_count == count_words(chunk.chunk_text)
        assert chunk.token_count <= 9


def test_create_chunks_for_filing_stores_short_section_as_one_chunk() -> None:
    db_session = make_session()
    filing = make_filing(db_session)
    section_text = "Item 1A. Risk Factors\n\nRisks include supply constraints and competition."
    section = make_section(
        db_session,
        filing,
        section_key="item_1a",
        section_order=0,
        normalized_text=section_text,
        start_offset=100,
    )

    chunks = FilingChunkingService(
        db_session,
        target_tokens=80,
        max_tokens=100,
        token_counter=count_words,
        clock=lambda: NOW,
    ).create_chunks_for_filing(filing, [section])

    assert len(chunks) == 1
    chunk = chunks[0]
    assert chunk.section_id == section.id
    assert chunk.chunk_index == 0
    assert chunk.chunk_text == section_text
    assert chunk.token_count == count_words(section_text)
    assert chunk.start_offset == section.start_offset
    assert chunk.end_offset == section.start_offset + len(section_text)
    assert chunk.text_hash == hashlib.sha256(section_text.encode("utf-8")).hexdigest()
    assert chunk.accession_number == filing.accession_number
    assert chunk.form_type == filing.form_type
    assert chunk.filing_date == filing.filing_date
    assert chunk.section_key == section.section_key
    assert chunk.sec_url == filing.sec_primary_document_url


def test_create_chunks_for_filing_splits_long_sections_without_crossing_boundaries() -> None:
    db_session = make_session()
    filing = make_filing(db_session)
    first_section_text = "\n\n".join(
        [
            "Item 7. Management's Discussion and Analysis",
            "Revenue grew because services, devices, and subscriptions improved.",
            "Operating expenses increased as research and development investments continued.",
            "Management expects macroeconomic uncertainty to affect demand.",
        ],
    )
    second_section_text = "\n\n".join(
        [
            "Item 1A. Risk Factors",
            "The company faces supply constraints and competitive pressure.",
            "Currency movements may affect reported results.",
        ],
    )
    first_section = make_section(
        db_session,
        filing,
        section_key="item_7",
        section_order=0,
        normalized_text=first_section_text,
        start_offset=200,
    )
    second_section = make_section(
        db_session,
        filing,
        section_key="item_1a",
        section_order=1,
        normalized_text=second_section_text,
        start_offset=200 + len(first_section_text) + 50,
    )

    chunks = FilingChunkingService(
        db_session,
        target_tokens=10,
        max_tokens=14,
        token_counter=count_words,
        clock=lambda: NOW,
    ).create_chunks_for_filing(filing, [first_section, second_section])

    assert len(chunks) > 2
    chunks_by_section = {
        first_section.id: [chunk for chunk in chunks if chunk.section_id == first_section.id],
        second_section.id: [chunk for chunk in chunks if chunk.section_id == second_section.id],
    }
    assert [chunk.chunk_index for chunk in chunks_by_section[first_section.id]] == list(
        range(len(chunks_by_section[first_section.id])),
    )
    assert [chunk.chunk_index for chunk in chunks_by_section[second_section.id]] == list(
        range(len(chunks_by_section[second_section.id])),
    )
    for section in [first_section, second_section]:
        section_chunks = chunks_by_section[section.id]
        assert section_chunks
        for chunk in section_chunks:
            assert section.start_offset <= chunk.start_offset <= chunk.end_offset <= section.end_offset
            local_start = chunk.start_offset - section.start_offset
            local_end = chunk.end_offset - section.start_offset
            assert section.normalized_text[local_start:local_end] == chunk.chunk_text
            assert chunk.token_count <= 14


def test_create_chunks_for_filing_replaces_existing_chunks_for_reprocessing() -> None:
    db_session = make_session()
    filing = make_filing(db_session)
    section_text = "Item 2. Properties\n\nThe company owns and leases facilities."
    section = make_section(
        db_session,
        filing,
        section_key="item_2",
        section_order=0,
        normalized_text=section_text,
        start_offset=20,
    )
    stale_chunk = DocumentChunk(
        filing_id=filing.id,
        section_id=section.id,
        chunk_index=0,
        chunk_text="stale",
        token_count=1,
        start_offset=20,
        end_offset=25,
        text_hash="0" * 64,
        accession_number=filing.accession_number,
        form_type=filing.form_type,
        filing_date=filing.filing_date,
        section_key=section.section_key,
        sec_url=filing.sec_primary_document_url,
        created_at=NOW,
        updated_at=NOW,
    )
    db_session.add(stale_chunk)
    db_session.flush()

    chunks = FilingChunkingService(
        db_session,
        target_tokens=50,
        max_tokens=60,
        token_counter=count_words,
        clock=lambda: NOW,
    ).create_chunks_for_filing(filing, [section])
    stored_chunks = db_session.scalars(
        select(DocumentChunk).where(DocumentChunk.filing_id == filing.id),
    ).all()

    assert len(chunks) == 1
    assert len(stored_chunks) == 1
    assert stored_chunks[0].chunk_text == section_text

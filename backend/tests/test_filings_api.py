from __future__ import annotations

from collections.abc import Generator
from datetime import UTC, date, datetime

from fastapi.testclient import TestClient

import app.api.routes.filings as filings_route
from app.db import get_db_session
from app.main import app
from app.models import DocumentChunk, Filing, FilingSection, Job

NOW = datetime(2026, 5, 14, 12, 0, tzinfo=UTC)


def make_filing() -> Filing:
    return Filing(
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


def make_section(
    *,
    section_id: int = 10,
    filing_id: int = 7,
    section_key: str = "item_1a",
    section_order: int = 0,
) -> FilingSection:
    return FilingSection(
        id=section_id,
        filing_id=filing_id,
        section_key=section_key,
        section_title="Item 1A. Risk Factors",
        section_order=section_order,
        normalized_text="Item 1A. Risk Factors\n\nRisk text.",
        start_offset=100,
        end_offset=135,
        extraction_confidence=90,
        extraction_method="regex_fallback",
        created_at=NOW,
        updated_at=NOW,
    )


def make_chunk(
    *,
    chunk_id: int = 20,
    filing_id: int = 7,
    section_id: int = 10,
    chunk_index: int = 0,
) -> DocumentChunk:
    return DocumentChunk(
        id=chunk_id,
        filing_id=filing_id,
        section_id=section_id,
        chunk_index=chunk_index,
        chunk_text="Risk text.",
        token_count=3,
        start_offset=120,
        end_offset=130,
        text_hash="a" * 64,
        accession_number="0000320193-24-000123",
        form_type="10-K",
        filing_date=date(2024, 11, 1),
        section_key="item_1a",
        sec_url="https://www.sec.gov/Archives/example.htm",
        created_at=NOW,
        updated_at=NOW,
    )


class FakeScalarResult:
    def __init__(self, items: list) -> None:
        self.items = items

    def all(self) -> list:
        return self.items


class FakeSession:
    def __init__(
        self,
        *,
        filing: Filing | None = None,
        sections: list[FilingSection] | None = None,
        chunks: list[DocumentChunk] | None = None,
    ) -> None:
        self.filing = filing
        self.sections = sections or []
        self.chunks = chunks or []
        self.added: list[Job] = []
        self.commit_calls = 0
        self.refresh_calls = 0

    def get(self, model, item_id: int):
        if model is Filing and self.filing is not None and self.filing.id == item_id:
            return self.filing
        if model is FilingSection:
            for section in self.sections:
                if section.id == item_id:
                    return section
        return None

    def scalars(self, statement) -> FakeScalarResult:
        statement_text = str(statement)
        if "document_chunks" in statement_text:
            return FakeScalarResult(self.chunks)
        if "filing_sections" in statement_text:
            return FakeScalarResult(self.sections)

        return FakeScalarResult([])

    def add(self, job: Job) -> None:
        job.id = 123
        self.added.append(job)

    def flush(self) -> None:
        pass

    def commit(self) -> None:
        self.commit_calls += 1

    def refresh(self, job: Job) -> None:
        self.refresh_calls += 1


def override_db_session(session: FakeSession) -> None:
    def _override() -> Generator[FakeSession, None, None]:
        yield session

    app.dependency_overrides[get_db_session] = _override


def test_process_filing_creates_job_and_schedules_background_task(monkeypatch) -> None:
    session = FakeSession(filing=make_filing())
    scheduled_job_ids: list[int] = []
    monkeypatch.setattr(filings_route, "run_filing_processing_job", scheduled_job_ids.append)
    override_db_session(session)
    client = TestClient(app)

    response = client.post("/filings/7/process?refresh=true")

    app.dependency_overrides.clear()
    assert response.status_code == 202
    assert response.json()["id"] == 123
    assert response.json()["job_type"] == "filing_processing"
    assert response.json()["company_id"] == 42
    assert response.json()["status"] == "pending"
    assert response.json()["payload"] == {
        "filing_id": 7,
        "accession_number": "0000320193-24-000123",
        "form_type": "10-K",
        "refresh": True,
        "stage": "queued",
    }
    assert session.commit_calls == 1
    assert session.refresh_calls == 1
    assert scheduled_job_ids == [123]


def test_process_filing_returns_404_for_unknown_filing() -> None:
    override_db_session(FakeSession(filing=None))
    client = TestClient(app)

    response = client.post("/filings/999/process")

    app.dependency_overrides.clear()
    assert response.status_code == 404
    assert response.json() == {"detail": "Filing not found"}


def test_list_filing_sections_returns_sections_in_order() -> None:
    override_db_session(
        FakeSession(
            filing=make_filing(),
            sections=[
                make_section(section_id=10, section_key="item_1", section_order=0),
                make_section(section_id=11, section_key="item_1a", section_order=1),
            ],
        ),
    )
    client = TestClient(app)

    response = client.get("/filings/7/sections")

    app.dependency_overrides.clear()
    assert response.status_code == 200
    assert [section["section_key"] for section in response.json()] == ["item_1", "item_1a"]
    assert response.json()[0]["normalized_text"].startswith("Item 1A.")
    assert response.json()[0]["extraction_method"] == "regex_fallback"


def test_list_filing_sections_returns_404_for_unknown_filing() -> None:
    override_db_session(FakeSession(filing=None, sections=[]))
    client = TestClient(app)

    response = client.get("/filings/999/sections")

    app.dependency_overrides.clear()
    assert response.status_code == 404
    assert response.json() == {"detail": "Filing not found"}


def test_list_filing_chunks_returns_chunks() -> None:
    section = make_section(section_id=10)
    override_db_session(
        FakeSession(
            filing=make_filing(),
            sections=[section],
            chunks=[
                make_chunk(chunk_id=20, section_id=10, chunk_index=0),
                make_chunk(chunk_id=21, section_id=10, chunk_index=1),
            ],
        ),
    )
    client = TestClient(app)

    response = client.get("/filings/7/chunks?section_id=10&limit=2")

    app.dependency_overrides.clear()
    assert response.status_code == 200
    assert [chunk["id"] for chunk in response.json()] == [20, 21]
    assert response.json()[0]["section_id"] == 10
    assert response.json()[0]["chunk_text"] == "Risk text."
    assert response.json()[0]["sec_url"].endswith("example.htm")


def test_list_filing_chunks_returns_404_for_unknown_filing() -> None:
    override_db_session(FakeSession(filing=None, chunks=[]))
    client = TestClient(app)

    response = client.get("/filings/999/chunks")

    app.dependency_overrides.clear()
    assert response.status_code == 404
    assert response.json() == {"detail": "Filing not found"}


def test_list_filing_chunks_returns_404_for_section_from_other_filing() -> None:
    override_db_session(
        FakeSession(
            filing=make_filing(),
            sections=[make_section(section_id=10, filing_id=999)],
            chunks=[],
        ),
    )
    client = TestClient(app)

    response = client.get("/filings/7/chunks?section_id=10")

    app.dependency_overrides.clear()
    assert response.status_code == 404
    assert response.json() == {"detail": "Filing section not found"}

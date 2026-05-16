from collections.abc import Generator
from datetime import UTC, datetime

from fastapi.testclient import TestClient

import app.api.routes.filings as filings_route
from app.db import get_db_session
from app.main import app
from app.models import DocumentChunk, Filing, FilingDocument, FilingSection, Job

NOW = datetime(2026, 5, 16, 12, 0, tzinfo=UTC)


def make_filing() -> Filing:
    return Filing(
        id=10,
        company_id=42,
        accession_number="0000320193-24-000123",
        form_type="10-K",
        filing_date=datetime(2024, 11, 1, tzinfo=UTC).date(),
        report_date=datetime(2024, 9, 28, tzinfo=UTC).date(),
        primary_document="aapl.htm",
        sec_filing_url="https://www.sec.gov/Archives/aapl-index.htm",
        sec_primary_document_url="https://www.sec.gov/Archives/aapl.htm",
        created_at=NOW,
        updated_at=NOW,
    )


def make_job() -> Job:
    return Job(
        id=77,
        job_type="filing_parse",
        company_id=42,
        status="pending",
        progress=0,
        retry_count=0,
        payload={
            "filing_id": 10,
            "accession_number": "0000320193-24-000123",
            "form_type": "10-K",
            "refresh": True,
            "stage": "queued",
        },
        error_message=None,
        created_at=NOW,
        updated_at=NOW,
        started_at=None,
        finished_at=None,
    )


def make_section() -> FilingSection:
    return FilingSection(
        id=20,
        filing_id=10,
        section_key="PART I|ITEM 1A",
        part="PART I",
        item="ITEM 1A",
        title="Risk Factors",
        section_order=1,
        start_page=2,
        end_page=3,
        start_display_page=None,
        end_display_page=None,
        markdown_text="ITEM 1A. Risk Factors\n\nRisk text.",
        token_count=12,
        created_at=NOW,
        updated_at=NOW,
    )


def make_chunk() -> DocumentChunk:
    return DocumentChunk(
        id=30,
        filing_id=10,
        section_id=20,
        chunk_index=0,
        chunk_text="Risk text.",
        token_count=3,
        accession_number="0000320193-24-000123",
        form_type="10-K",
        filing_date=datetime(2024, 11, 1, tzinfo=UTC).date(),
        section_label="PART I - ITEM 1A - Risk Factors",
        sec_url="https://www.sec.gov/Archives/aapl.htm",
        start_page=2,
        end_page=2,
        start_display_page=None,
        end_display_page=None,
        element_ids=["sec2md-p2-t0-test"],
        xbrl_tags=["us-gaap:RiskFactorsTextBlock"],
        source_start_offset=0,
        source_end_offset=10,
        has_table=False,
        created_at=NOW,
        updated_at=NOW,
    )


def make_document() -> FilingDocument:
    return FilingDocument(
        id=40,
        filing_id=10,
        raw_html="<html><body>Risk text.</body></html>",
        annotated_html=(
            '<html><body><p data-sec2md-block="sec2md-p2-t0-test">'
            "Risk text."
            "</p></body></html>"
        ),
        source_url="https://www.sec.gov/Archives/aapl.htm",
        content_sha256="x" * 64,
        parser_name="sec2md",
        parser_version="0.1.23",
        fetched_at=NOW,
        parsed_at=NOW,
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
        job: Job | None = None,
        sections: list[FilingSection] | None = None,
        chunks: list[DocumentChunk] | None = None,
        document: FilingDocument | None = None,
    ) -> None:
        self.filing = filing
        self.job = job
        self.sections = sections or []
        self.chunks = chunks or []
        self.document = document
        self.added: list[Job] = []
        self.commit_calls = 0
        self.refresh_calls = 0

    def get(self, model, instance_id: int):
        if model is Filing:
            return self.filing if self.filing and self.filing.id == instance_id else None
        return None

    def scalar(self, statement):
        statement_text = str(statement)
        if "filing_sections" in statement_text:
            return self.sections[0] if self.sections else None
        if "document_chunks" in statement_text:
            return self.chunks[0] if self.chunks else None
        if "filing_documents" in statement_text:
            return self.document
        return None

    def scalars(self, statement) -> FakeScalarResult:
        statement_text = str(statement)
        if "document_chunks" in statement_text:
            return FakeScalarResult(self.chunks)
        if "filing_sections" in statement_text:
            return FakeScalarResult(self.sections)
        return FakeScalarResult([])

    def add(self, job: Job) -> None:
        job.id = 77
        self.job = job
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


def test_parse_filing_creates_job_and_schedules_background_task(monkeypatch) -> None:
    session = FakeSession(filing=make_filing())
    scheduled_job_ids: list[int] = []
    monkeypatch.setattr(filings_route, "run_filing_parse_job", scheduled_job_ids.append)
    override_db_session(session)
    client = TestClient(app)

    response = client.post("/filings/10/parse?refresh=true")

    app.dependency_overrides.clear()
    assert response.status_code == 202
    assert response.json()["id"] == 77
    assert response.json()["job_type"] == "filing_parse"
    assert response.json()["payload"]["stage"] == "queued"
    assert response.json()["payload"]["refresh"] is True
    assert session.commit_calls == 1
    assert session.refresh_calls == 1
    assert scheduled_job_ids == [77]


def test_parse_filing_returns_404_for_unknown_filing() -> None:
    override_db_session(FakeSession(filing=None))
    client = TestClient(app)

    response = client.post("/filings/999/parse")

    app.dependency_overrides.clear()
    assert response.status_code == 404


def test_list_filing_sections_returns_section_summaries() -> None:
    override_db_session(FakeSession(filing=make_filing(), sections=[make_section()]))
    client = TestClient(app)

    response = client.get("/filings/10/sections")

    app.dependency_overrides.clear()
    assert response.status_code == 200
    assert response.json()[0]["section_key"] == "PART I|ITEM 1A"
    assert "markdown_text" not in response.json()[0]


def test_get_filing_section_returns_full_markdown() -> None:
    override_db_session(FakeSession(filing=make_filing(), sections=[make_section()]))
    client = TestClient(app)

    response = client.get("/filings/10/sections/20")

    app.dependency_overrides.clear()
    assert response.status_code == 200
    assert response.json()["markdown_text"].startswith("ITEM 1A")


def test_list_filing_chunks_returns_citation_metadata() -> None:
    override_db_session(FakeSession(filing=make_filing(), chunks=[make_chunk()]))
    client = TestClient(app)

    response = client.get("/filings/10/chunks?section_id=20")

    app.dependency_overrides.clear()
    assert response.status_code == 200
    chunk = response.json()[0]
    assert chunk["accession_number"] == "0000320193-24-000123"
    assert chunk["form_type"] == "10-K"
    assert chunk["filing_date"] == "2024-11-01"
    assert chunk["section_label"] == "PART I - ITEM 1A - Risk Factors"
    assert chunk["sec_url"].endswith("aapl.htm")
    assert chunk["element_ids"] == ["sec2md-p2-t0-test"]
    assert chunk["source_start_offset"] == 0


def test_get_chunk_highlighted_source_returns_visualized_html() -> None:
    override_db_session(
        FakeSession(
            filing=make_filing(),
            chunks=[make_chunk()],
            document=make_document(),
        )
    )
    client = TestClient(app)

    response = client.get("/filings/10/chunks/30/source")

    app.dependency_overrides.clear()
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert "sec2md-highlight" in response.text
    assert "sec2md-scroll" in response.text
    assert 'data-sec2md-block="sec2md-p2-t0-test"' in response.text


def test_get_chunk_highlighted_source_returns_404_without_annotated_html() -> None:
    override_db_session(
        FakeSession(
            filing=make_filing(),
            chunks=[make_chunk()],
            document=None,
        )
    )
    client = TestClient(app)

    response = client.get("/filings/10/chunks/30/source")

    app.dependency_overrides.clear()
    assert response.status_code == 404
    assert response.json() == {"detail": "Annotated filing document not found"}

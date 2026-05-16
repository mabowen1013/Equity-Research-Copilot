from datetime import UTC, datetime

import pytest

from app.models import DocumentChunk, Filing, FilingDocument, FilingSection, Job
from app.services import (
    FILING_PARSE_JOB_TYPE,
    FilingSec2MdService,
    UnsupportedFilingDocumentError,
)

NOW = datetime(2026, 5, 16, 12, 0, tzinfo=UTC)

SECTIONED_HTML = """
<html>
  <body>
    <div>PART I</div>
    <div>ITEM 1. Business</div>
    <p>Business overview sentence one. Business sentence two.</p>
    <div style="page-break-before:always">ITEM 1A. Risk Factors</div>
    <p>Risk one. Risk two.</p>
  </body>
</html>
"""

UNSECTIONED_HTML = """
<html>
  <body>
    <p>This filing has useful text but no recognizable item boundaries.</p>
  </body>
</html>
"""


class FakeSecClient:
    def __init__(self, text: str = SECTIONED_HTML) -> None:
        self.text = text
        self.urls: list[str] = []

    def get_text(self, url: str) -> str:
        self.urls.append(url)
        return self.text


class FakeSession:
    def __init__(
        self,
        *,
        filing: Filing | None = None,
        document: FilingDocument | None = None,
        job: Job | None = None,
    ) -> None:
        self.filing = filing
        self.document = document
        self.job = job
        self.added: list = []
        self.execute_calls: list[str] = []
        self.flush_calls = 0
        self.commit_calls = 0
        self.rollback_calls = 0
        self.next_id = 1

    def get(self, model, instance_id: int):
        if model is Filing:
            return self.filing if self.filing and self.filing.id == instance_id else None
        if model is Job:
            return self.job if self.job and self.job.id == instance_id else None
        return None

    def scalar(self, statement):
        return self.document

    def add(self, instance) -> None:
        if getattr(instance, "id", None) is None:
            instance.id = self.next_id
            self.next_id += 1
        if isinstance(instance, FilingDocument):
            self.document = instance
        if isinstance(instance, Job):
            self.job = instance
        self.added.append(instance)

    def execute(self, statement) -> None:
        self.execute_calls.append(str(statement))

    def flush(self) -> None:
        self.flush_calls += 1

    def commit(self) -> None:
        self.commit_calls += 1

    def rollback(self) -> None:
        self.rollback_calls += 1


def make_filing(*, primary_url: str | None = "https://www.sec.gov/Archives/aapl.htm") -> Filing:
    return Filing(
        id=10,
        company_id=42,
        accession_number="0000320193-24-000123",
        form_type="10-K",
        filing_date=datetime(2024, 11, 1, tzinfo=UTC).date(),
        report_date=datetime(2024, 9, 28, tzinfo=UTC).date(),
        primary_document="aapl.htm",
        sec_filing_url="https://www.sec.gov/Archives/aapl-index.htm",
        sec_primary_document_url=primary_url,
        created_at=NOW,
        updated_at=NOW,
    )


def test_get_or_fetch_document_downloads_and_caches_raw_html() -> None:
    filing = make_filing()
    sec_client = FakeSecClient()
    session = FakeSession(filing=filing)
    service = FilingSec2MdService(session, sec_client=sec_client, clock=lambda: NOW)

    document = service.get_or_fetch_document(filing)

    assert document.raw_html == SECTIONED_HTML
    assert document.source_url == "https://www.sec.gov/Archives/aapl.htm"
    assert document.parser_name == "sec2md"
    assert document.fetched_at == NOW
    assert document.parsed_at is None
    assert len(document.content_sha256) == 64
    assert sec_client.urls == ["https://www.sec.gov/Archives/aapl.htm"]
    assert session.flush_calls == 1


def test_get_or_fetch_document_reuses_cache_unless_refresh_requested() -> None:
    filing = make_filing()
    document = FilingDocument(
        id=5,
        filing_id=filing.id,
        raw_html="<html>cached</html>",
        source_url=filing.sec_primary_document_url,
        content_sha256="x" * 64,
        parser_name="sec2md",
        parser_version="0.1.22",
        fetched_at=NOW,
    )
    sec_client = FakeSecClient(text="<html>fresh</html>")
    session = FakeSession(filing=filing, document=document)
    service = FilingSec2MdService(session, sec_client=sec_client, clock=lambda: NOW)

    assert service.get_or_fetch_document(filing) is document
    assert sec_client.urls == []

    refreshed = service.get_or_fetch_document(filing, refresh=True)

    assert refreshed is document
    assert refreshed.raw_html == "<html>fresh</html>"
    assert sec_client.urls == ["https://www.sec.gov/Archives/aapl.htm"]


def test_get_or_fetch_document_rejects_missing_primary_document_url() -> None:
    filing = make_filing(primary_url=None)
    service = FilingSec2MdService(FakeSession(filing=filing), sec_client=FakeSecClient())

    with pytest.raises(UnsupportedFilingDocumentError, match="primary document URL"):
        service.get_or_fetch_document(filing)


def test_parse_and_store_document_extracts_sections_and_chunks() -> None:
    filing = make_filing()
    document = FilingDocument(
        id=5,
        filing_id=filing.id,
        raw_html=SECTIONED_HTML,
        source_url=filing.sec_primary_document_url,
        content_sha256="x" * 64,
        parser_name="sec2md",
        parser_version="0.1.22",
        fetched_at=NOW,
    )
    session = FakeSession(filing=filing, document=document)
    service = FilingSec2MdService(session, sec_client=FakeSecClient(), clock=lambda: NOW)

    result = service.parse_and_store_document(filing, document)

    sections = [item for item in session.added if isinstance(item, FilingSection)]
    chunks = [item for item in session.added if isinstance(item, DocumentChunk)]
    assert result.sections == sections
    assert result.chunks == chunks
    assert [section.section_key for section in sections] == ["PART I|ITEM 1", "PART I|ITEM 1A"]
    assert sections[0].title == "Business"
    assert chunks[0].accession_number == "0000320193-24-000123"
    assert chunks[0].form_type == "10-K"
    assert chunks[0].filing_date.isoformat() == "2024-11-01"
    assert chunks[0].section_label == "PART I - ITEM 1 - Business"
    assert chunks[0].sec_url == "https://www.sec.gov/Archives/aapl.htm"
    assert chunks[0].element_ids
    assert document.annotated_html is not None
    assert document.parsed_at == NOW
    assert any("DELETE FROM document_chunks" in call for call in session.execute_calls)
    assert any("DELETE FROM filing_sections" in call for call in session.execute_calls)


def test_parse_and_store_document_falls_back_to_full_document_section() -> None:
    filing = make_filing()
    document = FilingDocument(
        id=5,
        filing_id=filing.id,
        raw_html=UNSECTIONED_HTML,
        source_url=filing.sec_primary_document_url,
        content_sha256="x" * 64,
        parser_name="sec2md",
        parser_version="0.1.22",
        fetched_at=NOW,
    )
    session = FakeSession(filing=filing, document=document)
    service = FilingSec2MdService(session, sec_client=FakeSecClient(), clock=lambda: NOW)

    result = service.parse_and_store_document(filing, document)

    assert [section.section_key for section in result.sections] == ["FULL_DOCUMENT"]
    assert result.sections[0].title == "Full Document"
    assert len(result.chunks) == 1


def test_parse_and_store_document_rejects_pdf_content() -> None:
    filing = make_filing()
    document = FilingDocument(
        id=5,
        filing_id=filing.id,
        raw_html="%PDF-1.4",
        source_url=filing.sec_primary_document_url,
        content_sha256="x" * 64,
        parser_name="sec2md",
        parser_version="0.1.22",
        fetched_at=NOW,
    )
    service = FilingSec2MdService(FakeSession(filing=filing, document=document))

    with pytest.raises(UnsupportedFilingDocumentError, match="PDF"):
        service.parse_and_store_document(filing, document)


def test_create_job_stores_filing_parse_payload() -> None:
    filing = make_filing()
    session = FakeSession(filing=filing)
    service = FilingSec2MdService(session, sec_client=FakeSecClient(), clock=lambda: NOW)

    job = service.create_job(filing.id, refresh=True)

    assert job.job_type == FILING_PARSE_JOB_TYPE
    assert job.company_id == 42
    assert job.status == "pending"
    assert job.payload == {
        "filing_id": 10,
        "accession_number": "0000320193-24-000123",
        "form_type": "10-K",
        "refresh": True,
        "stage": "queued",
    }


def test_run_job_marks_succeeded_with_section_and_chunk_counts() -> None:
    filing = make_filing()
    job = Job(
        id=99,
        job_type=FILING_PARSE_JOB_TYPE,
        company_id=42,
        status="pending",
        progress=0,
        retry_count=0,
        payload={"filing_id": filing.id, "refresh": False},
        error_message=None,
        created_at=NOW,
        updated_at=NOW,
    )
    session = FakeSession(filing=filing, job=job)
    service = FilingSec2MdService(
        session,
        sec_client=FakeSecClient(text=SECTIONED_HTML),
        clock=lambda: NOW,
    )

    result = service.run_job(job.id)

    assert result is job
    assert job.status == "succeeded"
    assert job.progress == 100
    assert job.payload["stage"] == "completed"
    assert job.payload["sections_count"] == 2
    assert job.payload["chunks_count"] >= 2
    assert session.commit_calls == 3
    assert session.rollback_calls == 0


def test_run_job_marks_failed_for_missing_primary_document_url() -> None:
    filing = make_filing(primary_url=None)
    job = Job(
        id=99,
        job_type=FILING_PARSE_JOB_TYPE,
        company_id=42,
        status="pending",
        progress=0,
        retry_count=0,
        payload={"filing_id": filing.id, "refresh": False},
        error_message=None,
        created_at=NOW,
        updated_at=NOW,
    )
    session = FakeSession(filing=filing, job=job)
    service = FilingSec2MdService(session, sec_client=FakeSecClient(), clock=lambda: NOW)

    result = service.run_job(job.id)

    assert result is job
    assert job.status == "failed"
    assert job.payload["stage"] == "failed"
    assert job.payload["error_type"] == "UnsupportedFilingDocumentError"
    assert "primary document URL" in job.error_message
    assert session.rollback_calls == 1

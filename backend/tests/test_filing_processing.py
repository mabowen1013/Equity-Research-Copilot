from __future__ import annotations

from datetime import UTC, date, datetime
from types import SimpleNamespace

import pytest

from app.models import Filing, FilingDocument, Job
from app.services import (
    FILING_PROCESSING_JOB_TYPE,
    FilingDocumentDownloadError,
    FilingProcessingFilingNotFoundError,
    FilingProcessingJobNotFoundError,
    FilingProcessingService,
)

NOW = datetime(2026, 5, 14, 12, 0, tzinfo=UTC)


class FakeSession:
    def __init__(self, *, filing: Filing | None = None) -> None:
        self.filing = filing
        self.jobs: dict[int, Job] = {}
        self.added: list[Job] = []
        self.next_id = 1
        self.commit_calls = 0
        self.rollback_calls = 0
        self.flush_calls = 0

    def add(self, job: Job) -> None:
        if job.id is None:
            job.id = self.next_id
            self.next_id += 1

        self.jobs[job.id] = job
        self.added.append(job)

    def flush(self) -> None:
        self.flush_calls += 1

    def get(self, model, item_id: int):
        if model is Job:
            return self.jobs.get(item_id)
        if model is Filing and self.filing is not None and self.filing.id == item_id:
            return self.filing
        return None

    def commit(self) -> None:
        self.commit_calls += 1

    def rollback(self) -> None:
        self.rollback_calls += 1


class FakeFilingDocumentService:
    def __init__(
        self,
        *,
        document: FilingDocument | None = None,
        error: Exception | None = None,
        cache_hit: bool = False,
    ) -> None:
        self.document = document or FilingDocument(
            id=99,
            filing_id=7,
            source_url="https://www.sec.gov/example.htm",
            status="downloaded",
        )
        self.error = error
        self.cache_hit = cache_hit
        self.calls: list[dict] = []

    def get_or_download_primary_document(
        self,
        filing: Filing,
        *,
        refresh: bool = False,
    ):
        self.calls.append({"filing": filing, "refresh": refresh})
        if self.error is not None:
            raise self.error

        return SimpleNamespace(document=self.document, cache_hit=self.cache_hit)


class FakeFilingTextExtractionService:
    def __init__(
        self,
        *,
        error: Exception | None = None,
        events: list[str] | None = None,
    ) -> None:
        self.error = error
        self.events = events
        self.calls: list[dict] = []
        self.last_extraction_metrics = SimpleNamespace(
            as_payload=lambda: {
                "total_sections": 2,
                "sec_parser_validated_regex_offsets_count": 1,
                "regex_fallback_count": 1,
                "full_document_fallback_count": 0,
            },
        )

    def extract_filing_sections(
        self,
        filing: Filing,
        document: FilingDocument,
    ):
        if self.events is not None:
            self.events.append("extract_sections")
        self.calls.append({"filing": filing, "document": document})
        if self.error is not None:
            raise self.error

        return [SimpleNamespace(id=777), SimpleNamespace(id=778)]


class FakeFilingChunkingService:
    def __init__(
        self,
        *,
        error: Exception | None = None,
        events: list[str] | None = None,
    ) -> None:
        self.error = error
        self.events = events
        self.calls: list[dict] = []
        self.delete_calls: list[Filing] = []

    def delete_chunks_for_filing(self, filing: Filing) -> int:
        if self.events is not None:
            self.events.append("delete_chunks")
        self.delete_calls.append(filing)
        return 2

    def create_chunks_for_filing(
        self,
        filing: Filing,
        sections: list,
        *,
        delete_existing: bool = True,
    ):
        if self.events is not None:
            self.events.append("create_chunks")
        self.calls.append(
            {
                "filing": filing,
                "sections": sections,
                "delete_existing": delete_existing,
            },
        )
        if self.error is not None:
            raise self.error

        return [SimpleNamespace(id=880), SimpleNamespace(id=881), SimpleNamespace(id=882)]


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


def make_service(
    session: FakeSession,
    *,
    filing_document_service: FakeFilingDocumentService | None = None,
    filing_text_extraction_service: FakeFilingTextExtractionService | None = None,
    filing_chunking_service: FakeFilingChunkingService | None = None,
) -> FilingProcessingService:
    return FilingProcessingService(
        session,
        filing_document_service=filing_document_service or FakeFilingDocumentService(),
        filing_text_extraction_service=(
            filing_text_extraction_service or FakeFilingTextExtractionService()
        ),
        filing_chunking_service=filing_chunking_service or FakeFilingChunkingService(),
        clock=lambda: NOW,
    )


def test_create_job_stores_filing_payload() -> None:
    session = FakeSession(filing=make_filing())
    service = make_service(session)

    job = service.create_job(7, refresh=True)

    assert job.id == 1
    assert job.job_type == FILING_PROCESSING_JOB_TYPE
    assert job.company_id == 42
    assert job.status == "pending"
    assert job.progress == 0
    assert job.payload == {
        "filing_id": 7,
        "accession_number": "0000320193-24-000123",
        "form_type": "10-K",
        "refresh": True,
        "stage": "queued",
    }
    assert job.created_at == NOW
    assert job.updated_at == NOW
    assert session.added == [job]
    assert session.flush_calls == 1


def test_create_job_raises_for_unknown_filing() -> None:
    service = make_service(FakeSession(filing=None))

    with pytest.raises(FilingProcessingFilingNotFoundError, match="Filing 7"):
        service.create_job(7)


def test_run_job_downloads_document_and_marks_succeeded() -> None:
    filing = make_filing()
    session = FakeSession(filing=filing)
    document_service = FakeFilingDocumentService(cache_hit=True)
    events: list[str] = []
    text_service = FakeFilingTextExtractionService(events=events)
    chunking_service = FakeFilingChunkingService(events=events)
    service = make_service(
        session,
        filing_document_service=document_service,
        filing_text_extraction_service=text_service,
        filing_chunking_service=chunking_service,
    )
    job = service.create_job(7, refresh=True)

    result = service.run_job(job.id)

    assert result is job
    assert job.status == "succeeded"
    assert job.progress == 100
    assert job.error_message is None
    assert job.started_at == NOW
    assert job.finished_at == NOW
    assert job.payload["stage"] == "completed"
    assert job.payload["filing_document_id"] == 99
    assert job.payload["document_cache_hit"] is True
    assert job.payload["document_status"] == "downloaded"
    assert job.payload["section_ids"] == [777, 778]
    assert job.payload["sections_count"] == 2
    assert job.payload["chunk_ids"] == [880, 881, 882]
    assert job.payload["chunks_count"] == 3
    assert job.payload["parser_metrics"] == {
        "total_sections": 2,
        "sec_parser_validated_regex_offsets_count": 1,
        "regex_fallback_count": 1,
        "full_document_fallback_count": 0,
    }
    assert document_service.calls == [{"filing": filing, "refresh": True}]
    assert events == ["delete_chunks", "extract_sections", "create_chunks"]
    assert chunking_service.delete_calls == [filing]
    assert text_service.calls == [{"filing": filing, "document": document_service.document}]
    assert chunking_service.calls[0]["filing"] is filing
    assert chunking_service.calls[0]["delete_existing"] is False
    assert [section.id for section in chunking_service.calls[0]["sections"]] == [777, 778]
    assert session.commit_calls == 3
    assert session.rollback_calls == 0


def test_run_job_marks_failed_when_download_raises() -> None:
    session = FakeSession(filing=make_filing())
    document_service = FakeFilingDocumentService(
        error=FilingDocumentDownloadError("SEC document unavailable"),
    )
    service = make_service(session, filing_document_service=document_service)
    job = service.create_job(7)

    result = service.run_job(job.id)

    assert result is job
    assert job.status == "failed"
    assert job.progress == 10
    assert job.error_message == "SEC document unavailable"
    assert job.finished_at == NOW
    assert job.payload["stage"] == "failed"
    assert job.payload["error_type"] == "FilingDocumentDownloadError"
    assert session.rollback_calls == 1
    assert session.commit_calls == 2


def test_run_job_marks_failed_when_text_extraction_raises() -> None:
    filing = make_filing()
    session = FakeSession(filing=filing)
    document_service = FakeFilingDocumentService()
    text_service = FakeFilingTextExtractionService(error=RuntimeError("no text"))
    service = make_service(
        session,
        filing_document_service=document_service,
        filing_text_extraction_service=text_service,
    )
    job = service.create_job(7)

    result = service.run_job(job.id)

    assert result is job
    assert job.status == "failed"
    assert job.progress == 60
    assert job.error_message == "no text"
    assert job.payload["stage"] == "failed"
    assert job.payload["filing_document_id"] == 99
    assert job.payload["error_type"] == "RuntimeError"
    assert text_service.calls == [{"filing": filing, "document": document_service.document}]
    assert session.rollback_calls == 1
    assert session.commit_calls == 3


def test_run_job_marks_failed_when_chunking_raises() -> None:
    filing = make_filing()
    session = FakeSession(filing=filing)
    document_service = FakeFilingDocumentService()
    text_service = FakeFilingTextExtractionService()
    chunking_service = FakeFilingChunkingService(error=RuntimeError("chunking failed"))
    service = make_service(
        session,
        filing_document_service=document_service,
        filing_text_extraction_service=text_service,
        filing_chunking_service=chunking_service,
    )
    job = service.create_job(7)

    result = service.run_job(job.id)

    assert result is job
    assert job.status == "failed"
    assert job.progress == 60
    assert job.error_message == "chunking failed"
    assert job.payload["stage"] == "failed"
    assert job.payload["filing_document_id"] == 99
    assert job.payload["error_type"] == "RuntimeError"
    assert text_service.calls == [{"filing": filing, "document": document_service.document}]
    assert chunking_service.calls[0]["filing"] is filing
    assert chunking_service.calls[0]["delete_existing"] is False
    assert [section.id for section in chunking_service.calls[0]["sections"]] == [777, 778]
    assert session.rollback_calls == 1
    assert session.commit_calls == 3


def test_run_job_marks_failed_when_filing_is_missing() -> None:
    session = FakeSession(filing=make_filing())
    service = make_service(session)
    job = service.create_job(7)
    session.filing = None

    result = service.run_job(job.id)

    assert result is job
    assert job.status == "failed"
    assert job.error_message == "Filing 7 was not found."
    assert job.payload["error_type"] == "FilingProcessingFilingNotFoundError"
    assert session.rollback_calls == 1
    assert session.commit_calls == 2


def test_run_job_raises_for_unknown_job_id() -> None:
    service = make_service(FakeSession(filing=make_filing()))

    with pytest.raises(FilingProcessingJobNotFoundError, match="was not found"):
        service.run_job(999)

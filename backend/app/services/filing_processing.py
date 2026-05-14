from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session

from app.models import Filing, Job
from app.services.filing_document import FilingDocumentService
from app.services.filing_text import FilingTextExtractionService

FILING_PROCESSING_JOB_TYPE = "filing_processing"


def utc_now() -> datetime:
    return datetime.now(UTC)


class FilingProcessingJobNotFoundError(ValueError):
    """Raised when a filing processing job cannot be found."""


class FilingProcessingFilingNotFoundError(ValueError):
    """Raised when a filing cannot be found for processing."""


class FilingProcessingService:
    def __init__(
        self,
        db: Session,
        *,
        filing_document_service: FilingDocumentService | None = None,
        filing_text_extraction_service: FilingTextExtractionService | None = None,
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        self._db = db
        self._filing_document_service = filing_document_service
        self._filing_text_extraction_service = filing_text_extraction_service
        self._clock = clock

    def create_job(self, filing_id: int, *, refresh: bool = False) -> Job:
        filing = self._get_filing_or_raise(filing_id)
        now = self._clock()
        job = Job(
            job_type=FILING_PROCESSING_JOB_TYPE,
            company_id=filing.company_id,
            status="pending",
            progress=0,
            retry_count=0,
            payload={
                "filing_id": filing.id,
                "accession_number": filing.accession_number,
                "form_type": filing.form_type,
                "refresh": refresh,
                "stage": "queued",
            },
            error_message=None,
            created_at=now,
            updated_at=now,
        )

        self._db.add(job)
        self._db.flush()
        return job

    def run_job(self, job_id: int) -> Job:
        job = self._db.get(Job, job_id)
        if job is None:
            raise FilingProcessingJobNotFoundError(
                f"Filing processing job {job_id} was not found.",
            )

        filing_id = int((job.payload or {}).get("filing_id", 0))
        refresh = bool((job.payload or {}).get("refresh", False))

        try:
            self._mark_running(job)
            filing = self._get_filing_or_raise(filing_id)
            document_service = self._filing_document_service or FilingDocumentService(self._db)
            result = document_service.get_or_download_primary_document(
                filing,
                refresh=refresh,
            )
            self._mark_extracting_text(
                job,
                document_id=result.document.id,
                cache_hit=result.cache_hit,
                document_status=result.document.status,
            )
            text_extraction_service = (
                self._filing_text_extraction_service or FilingTextExtractionService(self._db)
            )
            section = text_extraction_service.extract_full_document_section(
                filing,
                result.document,
            )
            self._mark_succeeded(
                job,
                document_id=result.document.id,
                cache_hit=result.cache_hit,
                document_status=result.document.status,
                section_id=section.id,
            )
        except Exception as exc:
            self._db.rollback()
            failed_job = self._db.get(Job, job_id) or job
            self._mark_failed(failed_job, exc)
            return failed_job

        return job

    def _get_filing_or_raise(self, filing_id: int) -> Filing:
        filing = self._db.get(Filing, filing_id)
        if filing is None:
            raise FilingProcessingFilingNotFoundError(f"Filing {filing_id} was not found.")

        return filing

    def _mark_running(self, job: Job) -> None:
        now = self._clock()
        job.status = "running"
        job.progress = 10
        job.started_at = now
        job.updated_at = now
        job.error_message = None
        self._merge_payload(job, stage="downloading_document")
        self._db.commit()

    def _mark_succeeded(
        self,
        job: Job,
        *,
        document_id: int,
        cache_hit: bool,
        document_status: str,
        section_id: int,
    ) -> None:
        now = self._clock()
        job.status = "succeeded"
        job.progress = 100
        job.finished_at = now
        job.updated_at = now
        self._merge_payload(
            job,
            stage="completed",
            filing_document_id=document_id,
            document_cache_hit=cache_hit,
            document_status=document_status,
            full_document_section_id=section_id,
            sections_count=1,
        )
        self._db.commit()

    def _mark_extracting_text(
        self,
        job: Job,
        *,
        document_id: int,
        cache_hit: bool,
        document_status: str,
    ) -> None:
        now = self._clock()
        job.progress = 60
        job.updated_at = now
        self._merge_payload(
            job,
            stage="extracting_text",
            filing_document_id=document_id,
            document_cache_hit=cache_hit,
            document_status=document_status,
        )
        self._db.commit()

    def _mark_failed(self, job: Job, exc: Exception) -> None:
        now = self._clock()
        job.status = "failed"
        job.finished_at = now
        job.updated_at = now
        job.error_message = str(exc)
        self._merge_payload(
            job,
            stage="failed",
            error_type=type(exc).__name__,
        )
        self._db.commit()

    def _merge_payload(self, job: Job, **updates: Any) -> None:
        job.payload = {
            **(job.payload or {}),
            **updates,
        }

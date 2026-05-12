from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session

from app.models import Job
from app.services.company_lookup import CompanyLookupService, normalize_ticker
from app.services.filing_metadata import FilingMetadataService

SEC_INGESTION_JOB_TYPE = "sec_ingestion"


def utc_now() -> datetime:
    return datetime.now(UTC)


class SecIngestionJobNotFoundError(ValueError):
    """Raised when an ingestion job cannot be found."""


class SecIngestionService:
    def __init__(
        self,
        db: Session,
        *,
        company_lookup_service: CompanyLookupService | None = None,
        filing_metadata_service: FilingMetadataService | None = None,
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        self._db = db
        self._company_lookup_service = company_lookup_service
        self._filing_metadata_service = filing_metadata_service
        self._clock = clock

    def create_job(self, ticker: str, *, refresh: bool = False) -> Job:
        normalized_ticker = normalize_ticker(ticker)
        now = self._clock()
        job = Job(
            job_type=SEC_INGESTION_JOB_TYPE,
            status="pending",
            progress=0,
            retry_count=0,
            payload={
                "ticker": normalized_ticker,
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
            raise SecIngestionJobNotFoundError(f"SEC ingestion job {job_id} was not found.")

        ticker = str((job.payload or {}).get("ticker", ""))
        refresh = bool((job.payload or {}).get("refresh", False))

        try:
            self._mark_running(job)

            company_lookup_service = self._company_lookup_service or CompanyLookupService(self._db)
            filing_metadata_service = self._filing_metadata_service or FilingMetadataService(self._db)

            company = company_lookup_service.resolve_and_upsert_company(
                ticker,
                refresh=refresh,
            )
            self._mark_company_resolved(job, company)

            filings = filing_metadata_service.fetch_and_upsert_recent_filings(
                company,
                refresh=refresh,
            )
            self._mark_succeeded(job, company=company, filings_count=len(filings))
        except Exception as exc:
            self._db.rollback()
            failed_job = self._db.get(Job, job_id) or job
            self._mark_failed(failed_job, exc)
            return failed_job

        return job

    def _mark_running(self, job: Job) -> None:
        now = self._clock()
        job.status = "running"
        job.progress = 10
        job.started_at = now
        job.updated_at = now
        job.error_message = None
        self._merge_payload(job, stage="resolving_company")
        self._db.commit()

    def _mark_company_resolved(self, job: Job, company: Any) -> None:
        now = self._clock()
        job.company_id = company.id
        job.progress = 50
        job.updated_at = now
        self._merge_payload(
            job,
            stage="fetching_filings",
            company_id=company.id,
            cik=company.cik,
            company_name=company.name,
            exchange=getattr(company, "exchange", None),
        )
        self._db.commit()

    def _mark_succeeded(self, job: Job, *, company: Any, filings_count: int) -> None:
        now = self._clock()
        job.status = "succeeded"
        job.progress = 100
        job.finished_at = now
        job.updated_at = now
        self._merge_payload(
            job,
            stage="completed",
            filings_count=filings_count,
            sic=getattr(company, "sic", None),
            sic_description=getattr(company, "sic_description", None),
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

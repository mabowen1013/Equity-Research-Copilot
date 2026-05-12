from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from app.models import Company, Filing, Job
from app.services import (
    SEC_INGESTION_JOB_TYPE,
    CompanyLookupService,
    FilingMetadataService,
    SecIngestionJobNotFoundError,
    SecIngestionService,
)

NOW = datetime(2026, 5, 12, 12, 0, tzinfo=UTC)


class FakeSession:
    def __init__(self) -> None:
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

    def get(self, model, job_id: int) -> Job | None:
        return self.jobs.get(job_id)

    def commit(self) -> None:
        self.commit_calls += 1

    def rollback(self) -> None:
        self.rollback_calls += 1


class FakeIntegrationSession:
    def __init__(self) -> None:
        self.jobs: dict[int, Job] = {}
        self.companies: list[Company] = []
        self.filings: list[Filing] = []
        self.next_id = 1
        self.commit_calls = 0
        self.rollback_calls = 0

    def add(self, instance) -> None:
        if getattr(instance, "id", None) is None:
            instance.id = self.next_id
            self.next_id += 1

        if isinstance(instance, Job):
            self.jobs[instance.id] = instance
        elif isinstance(instance, Company):
            self.companies.append(instance)
        elif isinstance(instance, Filing):
            self.filings.append(instance)

    def flush(self) -> None:
        pass

    def get(self, model, job_id: int) -> Job | None:
        return self.jobs.get(job_id)

    def scalar(self, statement):
        statement_text = str(statement)
        if "FROM companies" in statement_text:
            return self.companies[0] if self.companies else None
        if "FROM filings" in statement_text:
            return None
        return None

    def commit(self) -> None:
        self.commit_calls += 1

    def rollback(self) -> None:
        self.rollback_calls += 1


class FakeCacheService:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def get_or_fetch_json(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(
            response_json=kwargs["fetch_json"](kwargs["url"]),
            cache_hit=False,
            record=None,
        )


class FakeSecClient:
    def __init__(self) -> None:
        self.urls: list[str] = []

    def get_json(self, url: str) -> dict:
        self.urls.append(url)
        if url.endswith("company_tickers.json"):
            return {
                "0": {
                    "cik_str": 320193,
                    "ticker": "AAPL",
                    "title": "Apple Inc.",
                    "exchange": "Nasdaq",
                }
            }

        return {
            "filings": {
                "recent": {
                    "accessionNumber": [
                        "0000320193-24-000123",
                        "0000320193-24-000111",
                        "0000320193-24-000099",
                    ],
                    "form": ["10-K", "10-Q", "4"],
                    "filingDate": ["2024-11-01", "2024-08-02", "2024-06-01"],
                    "reportDate": ["2024-09-28", "2024-06-29", "2024-05-30"],
                    "primaryDocument": [
                        "aapl-20240928.htm",
                        "aapl-20240629.htm",
                        "ownership.xml",
                    ],
                }
            }
        }


class FakeCompanyLookupService:
    def __init__(self, *, error: Exception | None = None) -> None:
        self.error = error
        self.calls: list[dict] = []

    def resolve_and_upsert_company(self, ticker: str, *, refresh: bool = False) -> Company:
        self.calls.append({"ticker": ticker, "refresh": refresh})
        if self.error is not None:
            raise self.error

        return Company(
            id=42,
            ticker=ticker,
            cik="0000320193",
            name="Apple Inc.",
        )


class FakeFilingMetadataService:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def fetch_and_upsert_recent_filings(
        self,
        company: Company,
        *,
        refresh: bool = False,
    ) -> list[Filing]:
        self.calls.append({"company": company, "refresh": refresh})
        return [Filing(accession_number="a"), Filing(accession_number="b")]


def make_service(
    session: FakeSession,
    *,
    company_lookup_service: FakeCompanyLookupService | None = None,
    filing_metadata_service: FakeFilingMetadataService | None = None,
) -> SecIngestionService:
    return SecIngestionService(
        session,
        company_lookup_service=company_lookup_service or FakeCompanyLookupService(),
        filing_metadata_service=filing_metadata_service or FakeFilingMetadataService(),
        clock=lambda: NOW,
    )


def test_create_job_normalizes_ticker_and_stores_initial_payload() -> None:
    session = FakeSession()
    service = make_service(session)

    job = service.create_job(" aapl ", refresh=True)

    assert job.id == 1
    assert job.job_type == SEC_INGESTION_JOB_TYPE
    assert job.status == "pending"
    assert job.progress == 0
    assert job.payload == {
        "ticker": "AAPL",
        "refresh": True,
        "stage": "queued",
    }
    assert job.created_at == NOW
    assert job.updated_at == NOW
    assert session.added == [job]
    assert session.flush_calls == 1


def test_run_job_resolves_company_fetches_filings_and_marks_succeeded() -> None:
    session = FakeSession()
    company_lookup_service = FakeCompanyLookupService()
    filing_metadata_service = FakeFilingMetadataService()
    service = make_service(
        session,
        company_lookup_service=company_lookup_service,
        filing_metadata_service=filing_metadata_service,
    )
    job = service.create_job("AAPL", refresh=True)

    result = service.run_job(job.id)

    assert result is job
    assert job.status == "succeeded"
    assert job.progress == 100
    assert job.company_id == 42
    assert job.error_message is None
    assert job.started_at == NOW
    assert job.finished_at == NOW
    assert job.payload["stage"] == "completed"
    assert job.payload["cik"] == "0000320193"
    assert job.payload["company_name"] == "Apple Inc."
    assert job.payload["filings_count"] == 2
    assert company_lookup_service.calls == [{"ticker": "AAPL", "refresh": True}]
    assert filing_metadata_service.calls[0]["company"].ticker == "AAPL"
    assert filing_metadata_service.calls[0]["refresh"] is True
    assert session.commit_calls == 3
    assert session.rollback_calls == 0


def test_run_job_marks_failed_when_ingestion_raises() -> None:
    session = FakeSession()
    company_lookup_service = FakeCompanyLookupService(error=RuntimeError("SEC unavailable"))
    service = make_service(session, company_lookup_service=company_lookup_service)
    job = service.create_job("AAPL")

    result = service.run_job(job.id)

    assert result is job
    assert job.status == "failed"
    assert job.progress == 10
    assert job.error_message == "SEC unavailable"
    assert job.finished_at == NOW
    assert job.payload["stage"] == "failed"
    assert job.payload["error_type"] == "RuntimeError"
    assert session.rollback_calls == 1
    assert session.commit_calls == 2


def test_run_job_raises_for_unknown_job_id() -> None:
    service = make_service(FakeSession())

    with pytest.raises(SecIngestionJobNotFoundError, match="was not found"):
        service.run_job(999)


def test_sec_ingestion_runs_full_mocked_sec_metadata_flow() -> None:
    session = FakeIntegrationSession()
    cache_service = FakeCacheService()
    sec_client = FakeSecClient()
    company_lookup_service = CompanyLookupService(
        session,
        sec_client=sec_client,
        cache_service=cache_service,
    )
    filing_metadata_service = FilingMetadataService(
        session,
        sec_client=sec_client,
        cache_service=cache_service,
    )
    service = SecIngestionService(
        session,
        company_lookup_service=company_lookup_service,
        filing_metadata_service=filing_metadata_service,
        clock=lambda: NOW,
    )

    job = service.create_job("AAPL")
    result = service.run_job(job.id)

    assert result.status == "succeeded"
    assert result.company_id == session.companies[0].id
    assert result.payload["filings_count"] == 2
    assert session.companies[0].ticker == "AAPL"
    assert session.companies[0].cik == "0000320193"
    assert [filing.form_type for filing in session.filings] == ["10-K", "10-Q"]
    assert all(filing.company_id == session.companies[0].id for filing in session.filings)
    assert len(cache_service.calls) == 2
    assert sec_client.urls == [
        "https://www.sec.gov/files/company_tickers.json",
        "https://data.sec.gov/submissions/CIK0000320193.json",
    ]

from collections.abc import Generator
from datetime import UTC, datetime

from fastapi.testclient import TestClient

import app.api.routes.companies as companies_route
from app.db import get_db_session
from app.main import app
from app.models import Company, Filing, Job


def make_company(
    *,
    company_id: int = 1,
    ticker: str = "AAPL",
    cik: str = "0000320193",
    name: str = "Apple Inc.",
    exchange: str | None = "Nasdaq",
) -> Company:
    now = datetime(2026, 5, 12, 12, 0, tzinfo=UTC)
    return Company(
        id=company_id,
        ticker=ticker,
        cik=cik,
        name=name,
        exchange=exchange,
        sic=None,
        sic_description=None,
        created_at=now,
        updated_at=now,
    )


def make_job(
    *,
    job_id: int = 1,
    company_id: int = 1,
    status: str = "succeeded",
) -> Job:
    now = datetime(2026, 5, 12, 13, 0, tzinfo=UTC)
    return Job(
        id=job_id,
        job_type="sec_ingestion",
        company_id=company_id,
        status=status,
        progress=100,
        retry_count=0,
        payload={"ticker": "AAPL", "stage": "completed"},
        error_message=None,
        created_at=now,
        updated_at=now,
        started_at=now,
        finished_at=now,
    )


def make_filing(
    *,
    filing_id: int = 1,
    company_id: int = 1,
    accession_number: str = "0000320193-24-000123",
    form_type: str = "10-K",
) -> Filing:
    now = datetime(2026, 5, 12, 13, 0, tzinfo=UTC)
    return Filing(
        id=filing_id,
        company_id=company_id,
        accession_number=accession_number,
        form_type=form_type,
        filing_date=datetime(2024, 11, 1, tzinfo=UTC).date(),
        report_date=datetime(2024, 9, 28, tzinfo=UTC).date(),
        primary_document="aapl-20240928.htm",
        sec_filing_url=(
            "https://www.sec.gov/Archives/edgar/data/"
            "320193/000032019324000123/0000320193-24-000123-index.htm"
        ),
        sec_primary_document_url=(
            "https://www.sec.gov/Archives/edgar/data/"
            "320193/000032019324000123/aapl-20240928.htm"
        ),
        created_at=now,
        updated_at=now,
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
        company: Company | None = None,
        companies: list[Company] | None = None,
        jobs: list[Job] | None = None,
        filings: list[Filing] | None = None,
    ) -> None:
        self.company = company
        self.companies = companies or []
        self.jobs = jobs or []
        self.filings = filings or []
        self.added: list[Job] = []
        self.commit_calls = 0
        self.refresh_calls = 0

    def scalar(self, statement) -> Company | None:
        return self.company

    def scalars(self, statement) -> FakeScalarResult:
        statement_text = str(statement)
        if "jobs" in statement_text:
            return FakeScalarResult(self.jobs)
        if "filings" in statement_text:
            return FakeScalarResult(self.filings)

        return FakeScalarResult(self.companies)

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


def test_search_companies_returns_matching_companies() -> None:
    override_db_session(FakeSession(companies=[make_company()]))
    client = TestClient(app)

    response = client.get("/companies/search?q=app")

    app.dependency_overrides.clear()
    assert response.status_code == 200
    assert response.json()[0]["ticker"] == "AAPL"
    assert response.json()[0]["name"] == "Apple Inc."


def test_search_companies_rejects_blank_query() -> None:
    override_db_session(FakeSession(companies=[]))
    client = TestClient(app)

    response = client.get("/companies/search?q=%20")

    app.dependency_overrides.clear()
    assert response.status_code == 400
    assert response.json() == {"detail": "Search query must not be empty"}


def test_get_company_returns_company_by_ticker() -> None:
    override_db_session(FakeSession(company=make_company()))
    client = TestClient(app)

    response = client.get("/companies/aapl")

    app.dependency_overrides.clear()
    assert response.status_code == 200
    assert response.json()["ticker"] == "AAPL"
    assert response.json()["cik"] == "0000320193"


def test_get_company_returns_404_for_unknown_ticker() -> None:
    override_db_session(FakeSession(company=None))
    client = TestClient(app)

    response = client.get("/companies/NVDA")

    app.dependency_overrides.clear()
    assert response.status_code == 404
    assert response.json() == {"detail": "Company not found"}


def test_get_company_rejects_blank_ticker() -> None:
    override_db_session(FakeSession(company=None))
    client = TestClient(app)

    response = client.get("/companies/%20")

    app.dependency_overrides.clear()
    assert response.status_code == 400
    assert response.json() == {"detail": "Ticker must not be empty."}


def test_ingest_company_creates_job_and_schedules_background_task(monkeypatch) -> None:
    session = FakeSession()
    scheduled_job_ids: list[int] = []
    monkeypatch.setattr(companies_route, "run_sec_ingestion_job", scheduled_job_ids.append)
    override_db_session(session)
    client = TestClient(app)

    response = client.post("/companies/aapl/ingest?refresh=true")

    app.dependency_overrides.clear()
    assert response.status_code == 202
    assert response.json()["id"] == 123
    assert response.json()["job_type"] == "sec_ingestion"
    assert response.json()["status"] == "pending"
    assert response.json()["payload"] == {
        "ticker": "AAPL",
        "refresh": True,
        "stage": "queued",
    }
    assert session.commit_calls == 1
    assert session.refresh_calls == 1
    assert scheduled_job_ids == [123]


def test_ingest_company_rejects_blank_ticker() -> None:
    override_db_session(FakeSession())
    client = TestClient(app)

    response = client.post("/companies/%20/ingest")

    app.dependency_overrides.clear()
    assert response.status_code == 400
    assert response.json() == {"detail": "Ticker must not be empty."}


def test_list_company_jobs_returns_company_jobs() -> None:
    override_db_session(FakeSession(company=make_company(), jobs=[make_job()]))
    client = TestClient(app)

    response = client.get("/companies/AAPL/jobs")

    app.dependency_overrides.clear()
    assert response.status_code == 200
    assert response.json()[0]["job_type"] == "sec_ingestion"
    assert response.json()[0]["company_id"] == 1
    assert response.json()[0]["status"] == "succeeded"


def test_list_company_jobs_returns_404_for_unknown_company() -> None:
    override_db_session(FakeSession(company=None, jobs=[]))
    client = TestClient(app)

    response = client.get("/companies/NVDA/jobs")

    app.dependency_overrides.clear()
    assert response.status_code == 404
    assert response.json() == {"detail": "Company not found"}


def test_list_company_filings_returns_company_filings() -> None:
    override_db_session(FakeSession(company=make_company(), filings=[make_filing()]))
    client = TestClient(app)

    response = client.get("/companies/AAPL/filings")

    app.dependency_overrides.clear()
    assert response.status_code == 200
    assert response.json()[0]["accession_number"] == "0000320193-24-000123"
    assert response.json()[0]["form_type"] == "10-K"
    assert response.json()[0]["sec_primary_document_url"].endswith("aapl-20240928.htm")


def test_list_company_filings_accepts_form_type_filter() -> None:
    override_db_session(FakeSession(company=make_company(), filings=[make_filing(form_type="10-Q")]))
    client = TestClient(app)

    response = client.get("/companies/AAPL/filings?form_type=10-q")

    app.dependency_overrides.clear()
    assert response.status_code == 200
    assert response.json()[0]["form_type"] == "10-Q"


def test_list_company_filings_rejects_blank_form_type() -> None:
    override_db_session(FakeSession(company=make_company(), filings=[]))
    client = TestClient(app)

    response = client.get("/companies/AAPL/filings?form_type=%20")

    app.dependency_overrides.clear()
    assert response.status_code == 400
    assert response.json() == {"detail": "Form type must not be empty"}

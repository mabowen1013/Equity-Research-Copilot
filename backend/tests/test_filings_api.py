from __future__ import annotations

from collections.abc import Generator
from datetime import UTC, date, datetime

from fastapi.testclient import TestClient

import app.api.routes.filings as filings_route
from app.db import get_db_session
from app.main import app
from app.models import Filing, Job

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


class FakeSession:
    def __init__(self, *, filing: Filing | None = None) -> None:
        self.filing = filing
        self.added: list[Job] = []
        self.commit_calls = 0
        self.refresh_calls = 0

    def get(self, model, item_id: int):
        if model is Filing and self.filing is not None and self.filing.id == item_id:
            return self.filing
        return None

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

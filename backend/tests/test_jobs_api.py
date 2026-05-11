from collections.abc import Generator
from datetime import UTC, datetime

from fastapi.testclient import TestClient

from app.db import get_db_session
from app.main import app
from app.models import Job


def make_job(job_id: int = 1, status: str = "pending") -> Job:
    now = datetime(2026, 5, 11, 12, 0, tzinfo=UTC)
    return Job(
        id=job_id,
        job_type="ingestion",
        company_id=42,
        status=status,
        progress=25,
        retry_count=0,
        payload={"ticker": "AAPL"},
        error_message=None,
        created_at=now,
        updated_at=now,
        started_at=None,
        finished_at=None,
    )


class FakeScalarResult:
    def __init__(self, jobs: list[Job]) -> None:
        self.jobs = jobs

    def all(self) -> list[Job]:
        return self.jobs


class FakeSession:
    def __init__(self, jobs: list[Job]) -> None:
        self.jobs = jobs

    def get(self, model, job_id: int) -> Job | None:
        return next((job for job in self.jobs if job.id == job_id), None)

    def scalars(self, statement) -> FakeScalarResult:
        return FakeScalarResult(self.jobs)


def override_db_session(jobs: list[Job]) -> None:
    def _override() -> Generator[FakeSession, None, None]:
        yield FakeSession(jobs)

    app.dependency_overrides[get_db_session] = _override


def test_get_job_returns_job_status() -> None:
    override_db_session([make_job()])
    client = TestClient(app)

    response = client.get("/jobs/1")

    app.dependency_overrides.clear()
    assert response.status_code == 200
    assert response.json()["id"] == 1
    assert response.json()["status"] == "pending"
    assert response.json()["payload"] == {"ticker": "AAPL"}


def test_get_job_returns_404_for_unknown_job() -> None:
    override_db_session([])
    client = TestClient(app)

    response = client.get("/jobs/999")

    app.dependency_overrides.clear()
    assert response.status_code == 404
    assert response.json() == {"detail": "Job not found"}


def test_list_jobs_returns_job_statuses() -> None:
    override_db_session([make_job(job_id=1), make_job(job_id=2, status="running")])
    client = TestClient(app)

    response = client.get("/jobs")

    app.dependency_overrides.clear()
    assert response.status_code == 200
    assert [job["id"] for job in response.json()] == [1, 2]

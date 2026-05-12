from collections.abc import Generator
from datetime import UTC, datetime

from fastapi.testclient import TestClient

from app.db import get_db_session
from app.main import app
from app.models import Company


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


class FakeScalarResult:
    def __init__(self, companies: list[Company]) -> None:
        self.companies = companies

    def all(self) -> list[Company]:
        return self.companies


class FakeSession:
    def __init__(
        self,
        *,
        company: Company | None = None,
        companies: list[Company] | None = None,
    ) -> None:
        self.company = company
        self.companies = companies or []

    def scalar(self, statement) -> Company | None:
        return self.company

    def scalars(self, statement) -> FakeScalarResult:
        return FakeScalarResult(self.companies)


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

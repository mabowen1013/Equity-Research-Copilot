from types import SimpleNamespace

import pytest

from app.models import Company
from app.services import (
    CompanyLookupError,
    CompanyLookupService,
    SecCompanyRecord,
    TickerNotFoundError,
    find_company_record,
    normalize_ticker,
    zero_pad_cik,
)


SEC_TICKER_FIXTURE = {
    "0": {"cik_str": 320193, "ticker": "AAPL", "title": "Apple Inc."},
    "1": {"cik_str": "0000789019", "ticker": "MSFT", "title": "MICROSOFT CORP"},
}


class FakeSecClient:
    def __init__(self) -> None:
        self.urls: list[str] = []

    def get_json(self, url: str) -> dict:
        self.urls.append(url)
        return SEC_TICKER_FIXTURE


class FakeCacheService:
    def __init__(self, payload: dict | None = None) -> None:
        self.payload = payload or SEC_TICKER_FIXTURE
        self.calls: list[dict] = []

    def get_or_fetch_json(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(response_json=self.payload, cache_hit=True, record=None)


class FakeSession:
    def __init__(self, company: Company | None = None) -> None:
        self.company = company
        self.added: list[Company] = []
        self.flush_calls = 0

    def scalar(self, statement) -> Company | None:
        return self.company

    def add(self, company: Company) -> None:
        self.company = company
        self.added.append(company)

    def flush(self) -> None:
        self.flush_calls += 1


def test_normalize_ticker_strips_and_uppercases() -> None:
    assert normalize_ticker(" aapl ") == "AAPL"


def test_normalize_ticker_rejects_blank_value() -> None:
    with pytest.raises(CompanyLookupError, match="Ticker must not be empty"):
        normalize_ticker(" ")


def test_zero_pad_cik_returns_ten_digit_string() -> None:
    assert zero_pad_cik(320193) == "0000320193"
    assert zero_pad_cik("0000789019") == "0000789019"


def test_zero_pad_cik_rejects_non_digits() -> None:
    with pytest.raises(CompanyLookupError, match="CIK must contain only digits"):
        zero_pad_cik("abc")


def test_find_company_record_returns_matching_sec_metadata() -> None:
    record = find_company_record(SEC_TICKER_FIXTURE, "msft")

    assert record == SecCompanyRecord(
        ticker="MSFT",
        cik="0000789019",
        name="MICROSOFT CORP",
        exchange=None,
    )


def test_find_company_record_raises_for_unknown_ticker() -> None:
    with pytest.raises(TickerNotFoundError, match="Ticker NVDA was not found"):
        find_company_record(SEC_TICKER_FIXTURE, "NVDA")


def test_resolve_company_record_uses_cached_sec_ticker_payload() -> None:
    cache_service = FakeCacheService()
    sec_client = FakeSecClient()
    service = CompanyLookupService(
        FakeSession(),
        sec_client=sec_client,
        cache_service=cache_service,
    )

    record = service.resolve_company_record("aapl", refresh=True)

    assert record.ticker == "AAPL"
    assert record.cik == "0000320193"
    assert cache_service.calls[0]["refresh"] is True
    assert cache_service.calls[0]["fetch_json"].__self__ is sec_client
    assert cache_service.calls[0]["fetch_json"].__name__ == "get_json"
    assert sec_client.urls == []


def test_upsert_company_inserts_new_company() -> None:
    session = FakeSession()
    service = CompanyLookupService(
        session,
        sec_client=FakeSecClient(),
        cache_service=FakeCacheService(),
    )

    company = service.upsert_company(
        SecCompanyRecord(
            ticker="AAPL",
            cik="0000320193",
            name="Apple Inc.",
            exchange="Nasdaq",
        )
    )

    assert session.added == [company]
    assert session.flush_calls == 1
    assert company.ticker == "AAPL"
    assert company.cik == "0000320193"
    assert company.name == "Apple Inc."
    assert company.exchange == "Nasdaq"


def test_upsert_company_updates_existing_company() -> None:
    existing = Company(
        ticker="AAPL",
        cik="0000320193",
        name="Old Name",
        exchange=None,
    )
    session = FakeSession(existing)
    service = CompanyLookupService(
        session,
        sec_client=FakeSecClient(),
        cache_service=FakeCacheService(),
    )

    company = service.upsert_company(
        SecCompanyRecord(
            ticker="AAPL",
            cik="0000320193",
            name="Apple Inc.",
            exchange="Nasdaq",
        )
    )

    assert company is existing
    assert session.added == []
    assert session.flush_calls == 1
    assert existing.name == "Apple Inc."
    assert existing.exchange == "Nasdaq"


def test_resolve_and_upsert_company_combines_lookup_and_persistence() -> None:
    session = FakeSession()
    service = CompanyLookupService(
        session,
        sec_client=FakeSecClient(),
        cache_service=FakeCacheService(),
    )

    company = service.resolve_and_upsert_company("AAPL")

    assert company.ticker == "AAPL"
    assert company.cik == "0000320193"
    assert session.flush_calls == 1

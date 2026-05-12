from datetime import date
from types import SimpleNamespace

import pytest

from app.models import Company, Filing
from app.services import (
    FilingMetadataError,
    FilingMetadataService,
    SecFilingRecord,
    build_sec_archive_base_url,
    build_sec_filing_detail_url,
    build_sec_primary_document_url,
    build_sec_submissions_url,
    normalize_accession_number,
    parse_recent_filing_records,
    parse_sec_date,
)


SEC_SUBMISSIONS_FIXTURE = {
    "filings": {
        "recent": {
            "accessionNumber": [
                "0000320193-24-000123",
                "0000320193-24-000111",
                "0000320193-24-000100",
                "0000320193-24-000099",
            ],
            "form": ["10-K", "10-Q", "8-K", "4"],
            "filingDate": ["2024-11-01", "2024-08-02", "2024-07-01", "2024-06-01"],
            "reportDate": ["2024-09-28", "2024-06-29", "", "2024-05-30"],
            "primaryDocument": [
                "aapl-20240928.htm",
                "aapl-20240629.htm",
                "aapl-8k.htm",
                "ownership.xml",
            ],
        }
    }
}


class FakeSecClient:
    def __init__(self) -> None:
        self.urls: list[str] = []

    def get_json(self, url: str) -> dict:
        self.urls.append(url)
        return SEC_SUBMISSIONS_FIXTURE


class FakeCacheService:
    def __init__(self, payload: dict | None = None) -> None:
        self.payload = payload or SEC_SUBMISSIONS_FIXTURE
        self.calls: list[dict] = []

    def get_or_fetch_json(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(response_json=self.payload, cache_hit=True, record=None)


class FakeSession:
    def __init__(self, filing: Filing | None = None) -> None:
        self.filing = filing
        self.added: list[Filing] = []
        self.flush_calls = 0

    def scalar(self, statement) -> Filing | None:
        return self.filing

    def add(self, filing: Filing) -> None:
        self.added.append(filing)

    def flush(self) -> None:
        self.flush_calls += 1


def make_company() -> Company:
    return Company(
        id=42,
        ticker="AAPL",
        cik="0000320193",
        name="Apple Inc.",
    )


def make_record() -> SecFilingRecord:
    return SecFilingRecord(
        accession_number="0000320193-24-000123",
        form_type="10-K",
        filing_date=date(2024, 11, 1),
        report_date=date(2024, 9, 28),
        primary_document="aapl-20240928.htm",
        sec_filing_url=(
            "https://www.sec.gov/Archives/edgar/data/"
            "320193/000032019324000123/0000320193-24-000123-index.htm"
        ),
        sec_primary_document_url=(
            "https://www.sec.gov/Archives/edgar/data/"
            "320193/000032019324000123/aapl-20240928.htm"
        ),
    )


def test_build_sec_submissions_url_uses_zero_padded_cik() -> None:
    assert (
        build_sec_submissions_url("320193")
        == "https://data.sec.gov/submissions/CIK0000320193.json"
    )


def test_build_sec_archive_urls_use_archive_cik_and_compact_accession() -> None:
    accession = "0000320193-24-000123"

    assert (
        build_sec_archive_base_url("0000320193", accession)
        == "https://www.sec.gov/Archives/edgar/data/320193/000032019324000123"
    )
    assert (
        build_sec_filing_detail_url("0000320193", accession)
        == "https://www.sec.gov/Archives/edgar/data/"
        "320193/000032019324000123/0000320193-24-000123-index.htm"
    )
    assert (
        build_sec_primary_document_url("0000320193", accession, "aapl-20240928.htm")
        == "https://www.sec.gov/Archives/edgar/data/"
        "320193/000032019324000123/aapl-20240928.htm"
    )


def test_build_sec_primary_document_url_returns_none_for_blank_document() -> None:
    assert build_sec_primary_document_url("0000320193", "0000320193-24-000123", "") is None


def test_normalize_accession_number_rejects_blank_value() -> None:
    with pytest.raises(FilingMetadataError, match="Accession number must not be empty"):
        normalize_accession_number(" ")


def test_parse_sec_date_parses_iso_dates_and_optional_blanks() -> None:
    assert parse_sec_date("2024-11-01", field_name="filingDate", required=True) == date(
        2024,
        11,
        1,
    )
    assert parse_sec_date("", field_name="reportDate", required=False) is None


def test_parse_sec_date_rejects_invalid_dates() -> None:
    with pytest.raises(FilingMetadataError, match="filingDate must use YYYY-MM-DD format"):
        parse_sec_date("11/01/2024", field_name="filingDate", required=True)


def test_parse_recent_filing_records_filters_supported_forms() -> None:
    records = parse_recent_filing_records(SEC_SUBMISSIONS_FIXTURE, cik="0000320193")

    assert [record.form_type for record in records] == ["10-K", "10-Q", "8-K"]
    assert records[0].accession_number == "0000320193-24-000123"
    assert records[0].filing_date == date(2024, 11, 1)
    assert records[0].report_date == date(2024, 9, 28)
    assert records[0].sec_filing_url.endswith("0000320193-24-000123-index.htm")
    assert records[2].report_date is None


def test_parse_recent_filing_records_requires_recent_payload() -> None:
    with pytest.raises(FilingMetadataError, match="missing filings.recent"):
        parse_recent_filing_records({}, cik="0000320193")


def test_fetch_recent_filing_records_uses_cached_submissions_payload() -> None:
    cache_service = FakeCacheService()
    sec_client = FakeSecClient()
    service = FilingMetadataService(
        FakeSession(),
        sec_client=sec_client,
        cache_service=cache_service,
    )

    records = service.fetch_recent_filing_records("0000320193", refresh=True)

    assert len(records) == 3
    assert cache_service.calls[0]["url"] == "https://data.sec.gov/submissions/CIK0000320193.json"
    assert cache_service.calls[0]["refresh"] is True
    assert cache_service.calls[0]["fetch_json"].__self__ is sec_client
    assert sec_client.urls == []


def test_upsert_filings_inserts_new_filing() -> None:
    session = FakeSession()
    service = FilingMetadataService(
        session,
        sec_client=FakeSecClient(),
        cache_service=FakeCacheService(),
    )

    filings = service.upsert_filings(make_company(), [make_record()])

    assert filings == session.added
    assert session.flush_calls == 1
    assert filings[0].company_id == 42
    assert filings[0].accession_number == "0000320193-24-000123"
    assert filings[0].form_type == "10-K"
    assert filings[0].filing_date == date(2024, 11, 1)
    assert filings[0].report_date == date(2024, 9, 28)
    assert filings[0].primary_document == "aapl-20240928.htm"
    assert filings[0].sec_primary_document_url.endswith("aapl-20240928.htm")


def test_upsert_filings_updates_existing_filing() -> None:
    existing = Filing(
        company_id=42,
        accession_number="0000320193-24-000123",
        form_type="8-K",
        filing_date=date(2024, 1, 1),
        sec_filing_url="https://old.example.test",
    )
    session = FakeSession(existing)
    service = FilingMetadataService(
        session,
        sec_client=FakeSecClient(),
        cache_service=FakeCacheService(),
    )

    filings = service.upsert_filings(make_company(), [make_record()])

    assert filings == [existing]
    assert session.added == []
    assert session.flush_calls == 1
    assert existing.form_type == "10-K"
    assert existing.filing_date == date(2024, 11, 1)
    assert existing.sec_filing_url.endswith("0000320193-24-000123-index.htm")


def test_upsert_filings_requires_persisted_company() -> None:
    company = Company(ticker="AAPL", cik="0000320193", name="Apple Inc.")
    service = FilingMetadataService(
        FakeSession(),
        sec_client=FakeSecClient(),
        cache_service=FakeCacheService(),
    )

    with pytest.raises(FilingMetadataError, match="Company must be persisted"):
        service.upsert_filings(company, [make_record()])


def test_fetch_and_upsert_recent_filings_combines_fetch_and_persistence() -> None:
    session = FakeSession()
    service = FilingMetadataService(
        session,
        sec_client=FakeSecClient(),
        cache_service=FakeCacheService(),
    )

    filings = service.fetch_and_upsert_recent_filings(make_company())

    assert len(filings) == 3
    assert session.flush_calls == 1
    assert filings[0].form_type == "10-K"

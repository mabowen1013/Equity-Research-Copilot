from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core import Settings
from app.models import Company, Filing
from app.services.company_lookup import zero_pad_cik
from app.services.sec_cache import SecResponseCacheService, build_sec_cache_key
from app.services.sec_client import SecClient

SEC_SUBMISSIONS_BASE_URL = "https://data.sec.gov/submissions"
SEC_ARCHIVES_BASE_URL = "https://www.sec.gov/Archives/edgar/data"
SUPPORTED_FILING_FORMS = frozenset({"10-K", "10-Q", "8-K"})


class FilingMetadataError(ValueError):
    """Raised when SEC filing metadata cannot be parsed or stored."""


@dataclass(frozen=True)
class SecFilingRecord:
    accession_number: str
    form_type: str
    filing_date: date
    report_date: date | None
    primary_document: str | None
    sec_filing_url: str
    sec_primary_document_url: str | None


def build_sec_submissions_url(cik: str) -> str:
    return f"{SEC_SUBMISSIONS_BASE_URL}/CIK{zero_pad_cik(cik)}.json"


def normalize_accession_number(accession_number: str) -> str:
    normalized = accession_number.strip()

    if not normalized:
        raise FilingMetadataError("Accession number must not be empty.")

    return normalized


def build_sec_archive_base_url(cik: str, accession_number: str) -> str:
    cik_path = str(int(zero_pad_cik(cik)))
    accession_path = normalize_accession_number(accession_number).replace("-", "")
    return f"{SEC_ARCHIVES_BASE_URL}/{cik_path}/{accession_path}"


def build_sec_filing_detail_url(cik: str, accession_number: str) -> str:
    accession = normalize_accession_number(accession_number)
    return f"{build_sec_archive_base_url(cik, accession)}/{accession}-index.htm"


def build_sec_primary_document_url(
    cik: str,
    accession_number: str,
    primary_document: str | None,
) -> str | None:
    if primary_document is None or not primary_document.strip():
        return None

    return f"{build_sec_archive_base_url(cik, accession_number)}/{primary_document.strip()}"


def parse_sec_date(value: Any, *, field_name: str, required: bool) -> date | None:
    if value is None or not str(value).strip():
        if required:
            raise FilingMetadataError(f"{field_name} is required.")
        return None

    try:
        return date.fromisoformat(str(value).strip())
    except ValueError as exc:
        raise FilingMetadataError(f"{field_name} must use YYYY-MM-DD format.") from exc


def parse_recent_filing_records(
    submissions_payload: dict[str, Any],
    *,
    cik: str,
) -> list[SecFilingRecord]:
    recent = submissions_payload.get("filings", {}).get("recent")
    if not isinstance(recent, dict):
        raise FilingMetadataError("SEC submissions payload is missing filings.recent.")

    forms = _require_recent_list(recent, "form")
    records: list[SecFilingRecord] = []

    for index, form in enumerate(forms):
        form_type = str(form).strip()
        if form_type not in SUPPORTED_FILING_FORMS:
            continue

        accession_number = normalize_accession_number(
            _get_recent_string(recent, "accessionNumber", index, required=True)
        )
        filing_date = parse_sec_date(
            _get_recent_string(recent, "filingDate", index, required=True),
            field_name="filingDate",
            required=True,
        )
        report_date = parse_sec_date(
            _get_recent_string(recent, "reportDate", index, required=False),
            field_name="reportDate",
            required=False,
        )
        primary_document = _get_recent_string(recent, "primaryDocument", index, required=False)

        records.append(
            SecFilingRecord(
                accession_number=accession_number,
                form_type=form_type,
                filing_date=filing_date,
                report_date=report_date,
                primary_document=primary_document,
                sec_filing_url=build_sec_filing_detail_url(cik, accession_number),
                sec_primary_document_url=build_sec_primary_document_url(
                    cik,
                    accession_number,
                    primary_document,
                ),
            )
        )

    return records


class FilingMetadataService:
    def __init__(
        self,
        db: Session,
        *,
        settings: Settings | None = None,
        sec_client: SecClient | None = None,
        cache_service: SecResponseCacheService | None = None,
    ) -> None:
        self._db = db
        self._sec_client = sec_client or SecClient(settings=settings)
        self._cache_service = cache_service or SecResponseCacheService(db, settings=settings)

    def fetch_recent_filing_records(
        self,
        cik: str,
        *,
        refresh: bool = False,
    ) -> list[SecFilingRecord]:
        url = build_sec_submissions_url(cik)
        cache_result = self._cache_service.get_or_fetch_json(
            cache_key=build_sec_cache_key(url),
            url=url,
            fetch_json=self._sec_client.get_json,
            refresh=refresh,
        )
        return parse_recent_filing_records(cache_result.response_json, cik=cik)

    def upsert_filings(
        self,
        company: Company,
        records: list[SecFilingRecord],
    ) -> list[Filing]:
        if company.id is None:
            raise FilingMetadataError("Company must be persisted before upserting filings.")

        filings: list[Filing] = []
        for record in records:
            statement = select(Filing).where(Filing.accession_number == record.accession_number)
            filing = self._db.scalar(statement)

            if filing is None:
                filing = Filing(accession_number=record.accession_number)
                self._db.add(filing)

            filing.company_id = int(company.id)
            filing.form_type = record.form_type
            filing.filing_date = record.filing_date
            filing.report_date = record.report_date
            filing.primary_document = record.primary_document
            filing.sec_filing_url = record.sec_filing_url
            filing.sec_primary_document_url = record.sec_primary_document_url
            filings.append(filing)

        self._db.flush()
        return filings

    def fetch_and_upsert_recent_filings(
        self,
        company: Company,
        *,
        refresh: bool = False,
    ) -> list[Filing]:
        records = self.fetch_recent_filing_records(company.cik, refresh=refresh)
        return self.upsert_filings(company, records)


def _require_recent_list(recent: dict[str, Any], key: str) -> list[Any]:
    value = recent.get(key)
    if not isinstance(value, list):
        raise FilingMetadataError(f"SEC submissions payload is missing recent.{key}.")

    return value


def _get_recent_string(
    recent: dict[str, Any],
    key: str,
    index: int,
    *,
    required: bool,
) -> str | None:
    values = _require_recent_list(recent, key)
    if index >= len(values):
        if required:
            raise FilingMetadataError(f"SEC submissions recent.{key} is shorter than form list.")
        return None

    value = values[index]
    if value is None or not str(value).strip():
        if required:
            raise FilingMetadataError(f"SEC submissions recent.{key} has a blank required value.")
        return None

    return str(value).strip()

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.core import Settings
from app.models import Company
from app.services.sec_cache import SecResponseCacheService, build_sec_cache_key
from app.services.sec_client import SecClient

SEC_COMPANY_TICKERS_URL = "https://www.sec.gov/files/company_tickers_exchange.json"


class CompanyLookupError(ValueError):
    """Base error for company lookup failures."""


class TickerNotFoundError(CompanyLookupError):
    """Raised when a ticker is not present in SEC ticker metadata."""


@dataclass(frozen=True)
class SecCompanyRecord:
    ticker: str
    cik: str
    name: str
    exchange: str | None = None


def normalize_ticker(ticker: str) -> str:
    normalized = ticker.strip().upper()

    if not normalized:
        raise CompanyLookupError("Ticker must not be empty.")

    return normalized


def zero_pad_cik(cik: int | str) -> str:
    normalized = str(cik).strip()

    if not normalized.isdigit():
        raise CompanyLookupError("CIK must contain only digits.")

    return normalized.zfill(10)


def find_company_record(payload: dict[str, Any], ticker: str) -> SecCompanyRecord:
    normalized_ticker = normalize_ticker(ticker)

    for value in _iter_company_metadata_rows(payload):
        payload_ticker = value.get("ticker")
        if payload_ticker is None or normalize_ticker(str(payload_ticker)) != normalized_ticker:
            continue

        cik = value.get("cik") or value.get("cik_str")
        name = value.get("name") or value.get("title")
        if cik is None or name is None or not str(name).strip():
            raise CompanyLookupError(f"SEC ticker metadata for {normalized_ticker} is incomplete.")

        exchange = value.get("exchange")
        return SecCompanyRecord(
            ticker=normalized_ticker,
            cik=zero_pad_cik(cik),
            name=str(name).strip(),
            exchange=str(exchange).strip() if exchange else None,
        )

    raise TickerNotFoundError(f"Ticker {normalized_ticker} was not found in SEC metadata.")


def _iter_company_metadata_rows(payload: dict[str, Any]) -> list[dict[str, Any]]:
    fields = payload.get("fields")
    data = payload.get("data")
    if isinstance(fields, list) and isinstance(data, list):
        normalized_fields = [str(field) for field in fields]
        rows: list[dict[str, Any]] = []
        for row in data:
            if isinstance(row, (list, tuple)):
                rows.append(dict(zip(normalized_fields, row, strict=False)))
        return rows

    return [value for value in payload.values() if isinstance(value, dict)]


class CompanyLookupService:
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

    def resolve_company_record(
        self,
        ticker: str,
        *,
        refresh: bool = False,
    ) -> SecCompanyRecord:
        cache_result = self._cache_service.get_or_fetch_json(
            cache_key=build_sec_cache_key(SEC_COMPANY_TICKERS_URL),
            url=SEC_COMPANY_TICKERS_URL,
            fetch_json=self._sec_client.get_json,
            refresh=refresh,
        )
        return find_company_record(cache_result.response_json, ticker)

    def upsert_company(self, record: SecCompanyRecord) -> Company:
        statement = select(Company).where(
            or_(
                Company.ticker == record.ticker,
                Company.cik == record.cik,
            )
        )
        company = self._db.scalar(statement)

        if company is None:
            company = Company()
            self._db.add(company)

        company.ticker = record.ticker
        company.cik = record.cik
        company.name = record.name
        company.exchange = record.exchange

        self._db.flush()
        return company

    def resolve_and_upsert_company(
        self,
        ticker: str,
        *,
        refresh: bool = False,
    ) -> Company:
        record = self.resolve_company_record(ticker, refresh=refresh)
        return self.upsert_company(record)

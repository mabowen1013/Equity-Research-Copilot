


from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from hashlib import sha256
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.core import Settings
from app.models import Company, Filing, FinancialFact, Job
from app.services.company_lookup import CompanyLookupError, normalize_ticker, zero_pad_cik
from app.services.sec_cache import SecResponseCacheService, build_sec_cache_key
from app.services.sec_client import SecClient

SEC_COMPANY_FACTS_BASE_URL = "https://data.sec.gov/api/xbrl/companyfacts"
XBRL_METRICS_JOB_TYPE = "xbrl_metrics_load"
USD_UNIT = "USD"
RATIO_UNIT = "ratio"
RATIO_PRECISION = Decimal("0.000001")
SUPPORTED_XBRL_FORMS = frozenset({"10-K", "10-Q", "10-K/A", "10-Q/A"})

RAW_METRIC_TAGS: dict[str, tuple[str, ...]] = {
    "cash_and_cash_equivalents": ("CashAndCashEquivalentsAtCarryingValue",),
    "revenue": (
        "RevenueFromContractWithCustomerExcludingAssessedTax",
        "SalesRevenueNet",
        "Revenues",
    ),
    "gross_profit": ("GrossProfit",),
    "operating_income": ("OperatingIncomeLoss",),
    "net_income": ("NetIncomeLoss",),
    "operating_cash_flow": ("NetCashProvidedByUsedInOperatingActivities",),
    "capital_expenditures": (
        "PaymentsToAcquirePropertyPlantAndEquipment",
        "PaymentsToAcquireProductiveAssets",
    ),
}
INSTANT_METRIC_KEYS = frozenset({"cash_and_cash_equivalents"})

COMPUTED_METRIC_KEYS = (
    "free_cash_flow",
    "gross_margin",
    "operating_margin",
    "net_margin",
)
CORE_METRIC_KEYS = (*RAW_METRIC_TAGS.keys(), *COMPUTED_METRIC_KEYS)

METRIC_LABELS = {
    "cash_and_cash_equivalents": "Cash and Cash Equivalents",
    "revenue": "Revenue",
    "gross_profit": "Gross Profit",
    "operating_income": "Operating Income",
    "net_income": "Net Income",
    "operating_cash_flow": "Operating Cash Flow",
    "capital_expenditures": "Capital Expenditures",
    "free_cash_flow": "Free Cash Flow",
    "gross_margin": "Gross Margin",
    "operating_margin": "Operating Margin",
    "net_margin": "Net Margin",
}

COMPUTED_TAXONOMY_TAGS = {
    "free_cash_flow": "computed:FreeCashFlow",
    "gross_margin": "computed:GrossMargin",
    "operating_margin": "computed:OperatingMargin",
    "net_margin": "computed:NetMargin",
}


def utc_now() -> datetime:
    return datetime.now(UTC)


class XbrlMetricsError(ValueError):
    """Base error for XBRL metrics loading and normalization failures."""


class XbrlCompanyNotFoundError(XbrlMetricsError):
    """Raised when metrics are requested for an unknown local company."""


class XbrlMetricsJobNotFoundError(XbrlMetricsError):
    """Raised when an XBRL metrics load job cannot be found."""


@dataclass(frozen=True)
class NormalizedXbrlFact:
    canonical_metric_key: str
    taxonomy_tag: str
    label: str
    period_start: date | None
    period_end: date
    source_fiscal_year: int | None
    fact_fiscal_year: int | None
    fiscal_period: str | None
    form_type: str | None
    filed_date: date | None
    unit: str
    value: Decimal
    source_accession_number: str | None
    source_fact_id: str
    is_computed: bool = False
    calculation_notes: str | None = None
    tag_priority: int = 0


@dataclass(frozen=True)
class SkippedXbrlFact:
    canonical_metric_key: str
    taxonomy_tag: str
    reason: str
    source_accession_number: str | None = None
    form_type: str | None = None
    period_start: date | None = None
    period_end: date | None = None
    source_fiscal_year: int | None = None
    fact_fiscal_year: int | None = None
    fiscal_period: str | None = None


@dataclass(frozen=True)
class ComputedMetricDiagnostic:
    canonical_metric_key: str
    reason: str
    message: str
    period_start: date | None = None
    period_end: date | None = None
    source_fiscal_year: int | None = None
    fact_fiscal_year: int | None = None
    fiscal_period: str | None = None
    unit: str | None = None
    source_accession_number: str | None = None
    source_fact_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class XbrlNormalizationResult:
    facts: list[NormalizedXbrlFact]
    skipped_facts: list[SkippedXbrlFact]
    computed_metric_diagnostics: list[ComputedMetricDiagnostic]


@dataclass(frozen=True)
class XbrlMetricsLoadResult:
    facts: list[FinancialFact]
    raw_facts_count: int
    stored_facts_count: int
    computed_facts_count: int
    missing_metrics: list[str]
    skipped_facts_count: int = 0
    skipped_fact_reasons: dict[str, int] | None = None
    skipped_fact_samples: list[dict[str, Any]] | None = None
    computed_diagnostics_count: int = 0
    computed_diagnostic_reasons: dict[str, int] | None = None
    computed_diagnostic_samples: list[dict[str, Any]] | None = None


def build_sec_company_facts_url(cik: str) -> str:
    return f"{SEC_COMPANY_FACTS_BASE_URL}/CIK{zero_pad_cik(cik)}.json"


def normalize_company_facts(payload: dict[str, Any]) -> list[NormalizedXbrlFact]:
    return normalize_company_facts_with_diagnostics(payload).facts


def normalize_company_facts_with_diagnostics(
    payload: dict[str, Any],
) -> XbrlNormalizationResult:
    us_gaap_facts = _get_us_gaap_facts(payload)
    raw_candidates: list[NormalizedXbrlFact] = []
    skipped_facts: list[SkippedXbrlFact] = []

    for metric_key, tags in RAW_METRIC_TAGS.items():
        for tag_priority, tag in enumerate(tags):
            tag_payload = us_gaap_facts.get(tag)
            if not isinstance(tag_payload, dict):
                if tag in us_gaap_facts:
                    skipped_facts.append(
                        _build_skipped_fact(
                            metric_key=metric_key,
                            taxonomy_tag=f"us-gaap:{tag}",
                            reason="invalid_tag_payload",
                            fact_payload=tag_payload,
                        )
                    )
                continue

            units = tag_payload.get("units")
            usd_facts = units.get(USD_UNIT) if isinstance(units, dict) else None
            if not isinstance(usd_facts, list):
                skipped_facts.append(
                    _build_skipped_fact(
                        metric_key=metric_key,
                        taxonomy_tag=f"us-gaap:{tag}",
                        reason=(
                            "missing_usd_unit"
                            if isinstance(units, dict) and USD_UNIT not in units
                            else "invalid_usd_facts"
                        ),
                        fact_payload=tag_payload,
                    )
                )
                continue

            label = str(tag_payload.get("label") or METRIC_LABELS[metric_key])
            taxonomy_tag = f"us-gaap:{tag}"
            for fact_payload in usd_facts:
                fact, skipped_fact = _parse_raw_fact_with_skip_reason(
                    metric_key=metric_key,
                    taxonomy_tag=taxonomy_tag,
                    label=label,
                    tag_priority=tag_priority,
                    fact_payload=fact_payload,
                )
                if fact is not None:
                    raw_candidates.append(fact)
                if skipped_fact is not None:
                    skipped_facts.append(skipped_fact)

    raw_facts = _select_preferred_raw_facts(raw_candidates)
    computed_facts, computed_metric_diagnostics = _compute_metrics_with_diagnostics(raw_facts)
    return XbrlNormalizationResult(
        facts=[*raw_facts, *computed_facts],
        skipped_facts=skipped_facts,
        computed_metric_diagnostics=computed_metric_diagnostics,
    )


class XbrlMetricsService:
    def __init__(
        self,
        db: Session,
        *,
        settings: Settings | None = None,
        sec_client: SecClient | None = None,
        cache_service: SecResponseCacheService | None = None,
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        self._db = db
        self._sec_client = sec_client or SecClient(settings=settings)
        self._cache_service = cache_service or SecResponseCacheService(db, settings=settings)
        self._clock = clock

    def create_job(self, ticker: str, *, refresh: bool = False) -> Job:
        company = self._get_company_by_ticker(ticker)
        now = self._clock()
        job = Job(
            job_type=XBRL_METRICS_JOB_TYPE,
            company_id=company.id,
            status="pending",
            progress=0,
            retry_count=0,
            payload={
                "ticker": company.ticker,
                "company_id": company.id,
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
            raise XbrlMetricsJobNotFoundError(f"XBRL metrics job {job_id} was not found.")

        company_id = int((job.payload or {}).get("company_id", 0))
        refresh = bool((job.payload or {}).get("refresh", False))

        try:
            self._mark_stage(job, stage="fetching_company_facts", progress=10, started=True)
            company = self._get_company_by_id(company_id)
            payload = self.fetch_company_facts(company.cik, refresh=refresh)

            self._mark_stage(job, stage="normalizing_metrics", progress=45)
            normalization = normalize_company_facts_with_diagnostics(payload)
            normalized_facts = normalization.facts
            raw_facts_count = sum(1 for fact in normalized_facts if not fact.is_computed)
            skipped_fact_reasons = summarize_skipped_facts(normalization.skipped_facts)

            self._mark_stage(
                job,
                stage="storing_metrics",
                progress=75,
                raw_facts_count=raw_facts_count,
                computed_facts_count=len(normalized_facts) - raw_facts_count,
                skipped_facts_count=len(normalization.skipped_facts),
                skipped_fact_reasons=skipped_fact_reasons,
                skipped_fact_samples=serialize_skipped_fact_samples(
                    normalization.skipped_facts,
                ),
                computed_diagnostics_count=len(normalization.computed_metric_diagnostics),
                computed_diagnostic_reasons=summarize_computed_metric_diagnostics(
                    normalization.computed_metric_diagnostics,
                ),
                computed_diagnostic_samples=serialize_computed_metric_diagnostic_samples(
                    normalization.computed_metric_diagnostics,
                ),
            )
            result = self.replace_company_metrics(
                company,
                normalized_facts,
                skipped_facts=normalization.skipped_facts,
                computed_metric_diagnostics=normalization.computed_metric_diagnostics,
            )
            self._mark_succeeded(job, result)
        except Exception as exc:
            self._db.rollback()
            failed_job = self._db.get(Job, job_id) or job
            self._mark_failed(failed_job, exc)
            return failed_job

        return job

    def load_company_metrics(
        self,
        company: Company,
        *,
        refresh: bool = False,
    ) -> XbrlMetricsLoadResult:
        payload = self.fetch_company_facts(company.cik, refresh=refresh)
        normalization = normalize_company_facts_with_diagnostics(payload)
        return self.replace_company_metrics(
            company,
            normalization.facts,
            skipped_facts=normalization.skipped_facts,
            computed_metric_diagnostics=normalization.computed_metric_diagnostics,
        )

    def fetch_company_facts(self, cik: str, *, refresh: bool = False) -> dict[str, Any]:
        url = build_sec_company_facts_url(cik)
        cache_result = self._cache_service.get_or_fetch_json(
            cache_key=build_sec_cache_key(url),
            url=url,
            fetch_json=self._sec_client.get_json,
            refresh=refresh,
        )
        return cache_result.response_json

    def replace_company_metrics(
        self,
        company: Company,
        normalized_facts: list[NormalizedXbrlFact],
        *,
        skipped_facts: list[SkippedXbrlFact] | None = None,
        computed_metric_diagnostics: list[ComputedMetricDiagnostic] | None = None,
    ) -> XbrlMetricsLoadResult:
        if company.id is None:
            raise XbrlMetricsError("Company must be persisted before loading XBRL metrics.")

        self._db.execute(delete(FinancialFact).where(FinancialFact.company_id == company.id))
        filing_lookup = self._build_filing_lookup(company, normalized_facts)
        now = self._clock()
        stored_facts: list[FinancialFact] = []

        for normalized_fact in normalized_facts:
            source_filing = (
                filing_lookup.get(normalized_fact.source_accession_number)
                if normalized_fact.source_accession_number
                else None
            )
            financial_fact = FinancialFact(
                company_id=company.id,
                canonical_metric_key=normalized_fact.canonical_metric_key,
                taxonomy_tag=normalized_fact.taxonomy_tag,
                label=normalized_fact.label,
                period_start=normalized_fact.period_start,
                period_end=normalized_fact.period_end,
                source_fiscal_year=normalized_fact.source_fiscal_year,
                fact_fiscal_year=normalized_fact.fact_fiscal_year,
                fiscal_period=normalized_fact.fiscal_period,
                form_type=normalized_fact.form_type,
                filed_date=normalized_fact.filed_date,
                unit=normalized_fact.unit,
                value=normalized_fact.value,
                source_accession_number=normalized_fact.source_accession_number,
                source_filing_id=source_filing.id if source_filing is not None else None,
                source_filing_url=(
                    source_filing.sec_filing_url if source_filing is not None else None
                ),
                source_fact_id=normalized_fact.source_fact_id,
                is_computed=normalized_fact.is_computed,
                calculation_notes=normalized_fact.calculation_notes,
                created_at=now,
                updated_at=now,
            )
            self._db.add(financial_fact)
            stored_facts.append(financial_fact)

        self._db.flush()
        present_metrics = {fact.canonical_metric_key for fact in normalized_facts}
        computed_facts_count = sum(1 for fact in normalized_facts if fact.is_computed)
        active_skipped_facts = skipped_facts or []
        active_computed_diagnostics = computed_metric_diagnostics or []
        return XbrlMetricsLoadResult(
            facts=stored_facts,
            raw_facts_count=len(normalized_facts) - computed_facts_count,
            stored_facts_count=len(stored_facts),
            computed_facts_count=computed_facts_count,
            missing_metrics=[
                metric_key
                for metric_key in CORE_METRIC_KEYS
                if metric_key not in present_metrics
            ],
            skipped_facts_count=len(active_skipped_facts),
            skipped_fact_reasons=summarize_skipped_facts(active_skipped_facts),
            skipped_fact_samples=serialize_skipped_fact_samples(active_skipped_facts),
            computed_diagnostics_count=len(active_computed_diagnostics),
            computed_diagnostic_reasons=summarize_computed_metric_diagnostics(
                active_computed_diagnostics,
            ),
            computed_diagnostic_samples=serialize_computed_metric_diagnostic_samples(
                active_computed_diagnostics,
            ),
        )

    def _get_company_by_ticker(self, ticker: str) -> Company:
        try:
            normalized_ticker = normalize_ticker(ticker)
        except CompanyLookupError as exc:
            raise XbrlMetricsError(str(exc)) from exc

        statement = select(Company).where(Company.ticker == normalized_ticker)
        company = self._db.scalar(statement)
        if company is None:
            raise XbrlCompanyNotFoundError(f"Company {normalized_ticker} was not found.")
        return company

    def _get_company_by_id(self, company_id: int) -> Company:
        company = self._db.get(Company, company_id)
        if company is None:
            raise XbrlCompanyNotFoundError(f"Company {company_id} was not found.")
        return company

    def _build_filing_lookup(
        self,
        company: Company,
        normalized_facts: list[NormalizedXbrlFact],
    ) -> dict[str, Filing]:
        accessions = sorted(
            {
                fact.source_accession_number
                for fact in normalized_facts
                if fact.source_accession_number
            }
        )
        if not accessions:
            return {}

        statement = select(Filing).where(
            Filing.company_id == company.id,
            Filing.accession_number.in_(accessions),
        )
        filings = self._db.scalars(statement).all()
        return {filing.accession_number: filing for filing in filings}

    def _mark_stage(
        self,
        job: Job,
        *,
        stage: str,
        progress: int,
        started: bool = False,
        **payload_updates: Any,
    ) -> None:
        now = self._clock()
        job.status = "running"
        job.progress = progress
        job.updated_at = now
        if started:
            job.started_at = now
            job.error_message = None
        self._merge_payload(job, stage=stage, **payload_updates)
        self._db.commit()

    def _mark_succeeded(self, job: Job, result: XbrlMetricsLoadResult) -> None:
        now = self._clock()
        job.status = "succeeded"
        job.progress = 100
        job.finished_at = now
        job.updated_at = now
        self._merge_payload(
            job,
            stage="completed",
            raw_facts_count=result.raw_facts_count,
            stored_facts_count=result.stored_facts_count,
            computed_facts_count=result.computed_facts_count,
            missing_metrics=result.missing_metrics,
            skipped_facts_count=result.skipped_facts_count,
            skipped_fact_reasons=result.skipped_fact_reasons or {},
            skipped_fact_samples=result.skipped_fact_samples or [],
            computed_diagnostics_count=result.computed_diagnostics_count,
            computed_diagnostic_reasons=result.computed_diagnostic_reasons or {},
            computed_diagnostic_samples=result.computed_diagnostic_samples or [],
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


def _get_us_gaap_facts(payload: dict[str, Any]) -> dict[str, Any]:
    facts = payload.get("facts")
    if not isinstance(facts, dict):
        raise XbrlMetricsError("SEC company facts payload is missing facts.")

    us_gaap = facts.get("us-gaap")
    if not isinstance(us_gaap, dict):
        raise XbrlMetricsError("SEC company facts payload is missing facts.us-gaap.")

    return us_gaap


def _parse_raw_fact(
    *,
    metric_key: str,
    taxonomy_tag: str,
    label: str,
    tag_priority: int,
    fact_payload: Any,
) -> NormalizedXbrlFact | None:
    fact, _ = _parse_raw_fact_with_skip_reason(
        metric_key=metric_key,
        taxonomy_tag=taxonomy_tag,
        label=label,
        tag_priority=tag_priority,
        fact_payload=fact_payload,
    )
    return fact


def _parse_raw_fact_with_skip_reason(
    *,
    metric_key: str,
    taxonomy_tag: str,
    label: str,
    tag_priority: int,
    fact_payload: Any,
) -> tuple[NormalizedXbrlFact | None, SkippedXbrlFact | None]:
    if not isinstance(fact_payload, dict):
        return None, _build_skipped_fact(
            metric_key=metric_key,
            taxonomy_tag=taxonomy_tag,
            reason="invalid_fact_payload",
            fact_payload=fact_payload,
        )
    if "segment" in fact_payload or "segments" in fact_payload:
        return None, _build_skipped_fact(
            metric_key=metric_key,
            taxonomy_tag=taxonomy_tag,
            reason="segmented_fact",
            fact_payload=fact_payload,
        )

    form_type = _parse_optional_string(fact_payload.get("form"))
    if form_type is not None:
        form_type = form_type.upper()
    if form_type not in SUPPORTED_XBRL_FORMS:
        return None, _build_skipped_fact(
            metric_key=metric_key,
            taxonomy_tag=taxonomy_tag,
            reason="unsupported_form",
            fact_payload=fact_payload,
        )

    period_start = _parse_date(fact_payload.get("start"))
    period_end = _parse_date(fact_payload.get("end"))
    if (
        period_end is None
        or (period_start is None and metric_key not in INSTANT_METRIC_KEYS)
        or (period_start is not None and period_start > period_end)
    ):
        return None, _build_skipped_fact(
            metric_key=metric_key,
            taxonomy_tag=taxonomy_tag,
            reason="invalid_period",
            fact_payload=fact_payload,
        )

    value = _parse_decimal(fact_payload.get("val"))
    if value is None:
        return None, _build_skipped_fact(
            metric_key=metric_key,
            taxonomy_tag=taxonomy_tag,
            reason="invalid_value",
            fact_payload=fact_payload,
        )

    source_fiscal_year = _parse_int(fact_payload.get("fy"))
    fiscal_period = _parse_optional_string(fact_payload.get("fp"))
    if fiscal_period is not None:
        fiscal_period = fiscal_period.upper()

    filed_date = _parse_date(fact_payload.get("filed"))
    accession_number = _parse_optional_string(fact_payload.get("accn"))
    source_fact_id = _build_source_fact_id(
        metric_key=metric_key,
        taxonomy_tag=taxonomy_tag,
        unit=USD_UNIT,
        accession_number=accession_number,
        period_start=period_start,
        period_end=period_end,
        source_fiscal_year=source_fiscal_year,
        fiscal_period=fiscal_period,
        filed_date=filed_date,
        value=value,
    )

    return (
        NormalizedXbrlFact(
            canonical_metric_key=metric_key,
            taxonomy_tag=taxonomy_tag,
            label=label,
            period_start=period_start,
            period_end=period_end,
            source_fiscal_year=source_fiscal_year,
            fact_fiscal_year=source_fiscal_year,
            fiscal_period=fiscal_period,
            form_type=form_type,
            filed_date=filed_date,
            unit=USD_UNIT,
            value=value,
            source_accession_number=accession_number,
            source_fact_id=source_fact_id,
            is_computed=False,
            calculation_notes=None,
            tag_priority=tag_priority,
        ),
        None,
    )


def _build_skipped_fact(
    *,
    metric_key: str,
    taxonomy_tag: str,
    reason: str,
    fact_payload: Any,
) -> SkippedXbrlFact:
    payload = fact_payload if isinstance(fact_payload, dict) else {}
    form_type = _parse_optional_string(payload.get("form"))
    fiscal_period = _parse_optional_string(payload.get("fp"))

    return SkippedXbrlFact(
        canonical_metric_key=metric_key,
        taxonomy_tag=taxonomy_tag,
        reason=reason,
        source_accession_number=_parse_optional_string(payload.get("accn")),
        form_type=form_type.upper() if form_type is not None else None,
        period_start=_parse_date(payload.get("start")),
        period_end=_parse_date(payload.get("end")),
        source_fiscal_year=_parse_int(payload.get("fy")),
        fact_fiscal_year=None,
        fiscal_period=fiscal_period.upper() if fiscal_period is not None else None,
    )


def _select_preferred_raw_facts(
    candidates: list[NormalizedXbrlFact],
) -> list[NormalizedXbrlFact]:
    grouped: dict[tuple, list[NormalizedXbrlFact]] = {}
    fact_fiscal_year_by_period = _derive_fact_fiscal_years_by_period(candidates)

    for candidate in candidates:
        key = (
            candidate.canonical_metric_key,
            candidate.period_start,
            candidate.period_end,
            candidate.unit,
        )
        grouped.setdefault(key, []).append(candidate)

    selected: list[NormalizedXbrlFact] = []
    for group_candidates in grouped.values():
        preferred = max(group_candidates, key=_raw_fact_rank)
        selected.append(
            replace(
                preferred,
                fact_fiscal_year=_derive_fact_fiscal_year(
                    preferred,
                    group_candidates,
                    fact_fiscal_year_by_period=fact_fiscal_year_by_period,
                ),
            )
        )

    return sorted(
        selected,
        key=lambda fact: (
            fact.canonical_metric_key,
            fact.period_end,
            fact.filed_date or date.min,
            fact.source_fact_id,
        ),
    )


def _derive_fact_fiscal_years_by_period(
    candidates: list[NormalizedXbrlFact],
) -> dict[tuple, int]:
    years_by_period: dict[tuple, list[int]] = {}
    for candidate in candidates:
        if candidate.source_fiscal_year is None:
            continue
        years_by_period.setdefault(_fact_period_key(candidate), []).append(
            candidate.source_fiscal_year
        )
    return {
        period_key: min(source_years)
        for period_key, source_years in years_by_period.items()
        if source_years
    }


def _fact_period_key(fact: NormalizedXbrlFact) -> tuple:
    return (fact.period_start, fact.period_end, fact.fiscal_period)


def _derive_fact_fiscal_year(
    preferred: NormalizedXbrlFact,
    candidates: list[NormalizedXbrlFact],
    *,
    fact_fiscal_year_by_period: dict[tuple, int],
) -> int | None:
    # Later filings often repeat prior-year comparison facts with the current filing's FY.
    # The earliest FY attached to the same exact period is the fact's own fiscal year.
    period_year = fact_fiscal_year_by_period.get(_fact_period_key(preferred))
    if period_year is not None:
        return period_year

    matching_period_years = [
        candidate.source_fiscal_year
        for candidate in candidates
        if candidate.source_fiscal_year is not None
        and candidate.fiscal_period == preferred.fiscal_period
    ]
    if matching_period_years:
        return min(matching_period_years)

    source_years = [
        candidate.source_fiscal_year
        for candidate in candidates
        if candidate.source_fiscal_year is not None
    ]
    if source_years:
        return min(source_years)

    return preferred.period_end.year


def _raw_fact_rank(fact: NormalizedXbrlFact) -> tuple[int, date, str]:
    return (
        -fact.tag_priority,
        fact.filed_date or date.min,
        fact.source_accession_number or "",
    )


def _compute_metrics(raw_facts: list[NormalizedXbrlFact]) -> list[NormalizedXbrlFact]:
    computed_facts, _ = _compute_metrics_with_diagnostics(raw_facts)
    return computed_facts


def _compute_metrics_with_diagnostics(
    raw_facts: list[NormalizedXbrlFact],
) -> tuple[list[NormalizedXbrlFact], list[ComputedMetricDiagnostic]]:
    facts_by_metric: dict[str, list[NormalizedXbrlFact]] = {}
    for fact in raw_facts:
        facts_by_metric.setdefault(fact.canonical_metric_key, []).append(fact)

    computed: list[NormalizedXbrlFact] = []
    diagnostics: list[ComputedMetricDiagnostic] = []

    free_cash_flow, free_cash_flow_diagnostics = _compute_free_cash_flow_with_diagnostics(
        facts_by_metric
    )
    computed.extend(free_cash_flow)
    diagnostics.extend(free_cash_flow_diagnostics)

    for metric_key, numerator_key in [
        ("gross_margin", "gross_profit"),
        ("operating_margin", "operating_income"),
        ("net_margin", "net_income"),
    ]:
        margin_facts, margin_diagnostics = _compute_margin_with_diagnostics(
            metric_key=metric_key,
            numerator_key=numerator_key,
            facts_by_metric=facts_by_metric,
        )
        computed.extend(margin_facts)
        diagnostics.extend(margin_diagnostics)

    return computed, diagnostics


def _compute_free_cash_flow(
    facts_by_metric: dict[str, list[NormalizedXbrlFact]],
) -> list[NormalizedXbrlFact]:
    computed_facts, _ = _compute_free_cash_flow_with_diagnostics(facts_by_metric)
    return computed_facts


def _compute_free_cash_flow_with_diagnostics(
    facts_by_metric: dict[str, list[NormalizedXbrlFact]],
) -> tuple[list[NormalizedXbrlFact], list[ComputedMetricDiagnostic]]:
    capex_by_key = {
        _computed_match_key(fact): fact
        for fact in facts_by_metric.get("capital_expenditures", [])
    }
    computed: list[NormalizedXbrlFact] = []
    diagnostics: list[ComputedMetricDiagnostic] = []

    for operating_cash_flow in facts_by_metric.get("operating_cash_flow", []):
        key = _computed_match_key(operating_cash_flow)
        capex = capex_by_key.get(key)
        if capex is None:
            diagnostics.append(
                _build_computed_metric_diagnostic(
                    metric_key="free_cash_flow",
                    reason="missing_matching_capex",
                    message=(
                        f"Skipped free_cash_flow for {_format_fact_period(operating_cash_flow)} "
                        "because no capital_expenditures fact matched the same period and unit."
                    ),
                    source_facts=[operating_cash_flow],
                )
            )
            continue
        if capex.value < 0:
            diagnostics.append(
                _build_computed_metric_diagnostic(
                    metric_key="free_cash_flow",
                    reason="negative_capex_value",
                    message=(
                        f"Skipped free_cash_flow for {_format_fact_period(operating_cash_flow)} "
                        "because capex value was negative under "
                        f"{capex.taxonomy_tag}."
                    ),
                    source_facts=[operating_cash_flow, capex],
                )
            )
            continue

        value = operating_cash_flow.value - capex.value
        computed.append(
            _build_computed_fact(
                metric_key="free_cash_flow",
                value=value,
                unit=USD_UNIT,
                source_facts=[operating_cash_flow, capex],
                calculation_notes=(
                    "Computed as operating_cash_flow minus capital_expenditures "
                    f"from source facts {operating_cash_flow.source_fact_id} and "
                    f"{capex.source_fact_id}."
                ),
            )
        )

    return computed, diagnostics


def _compute_margin(
    *,
    metric_key: str,
    numerator_key: str,
    facts_by_metric: dict[str, list[NormalizedXbrlFact]],
) -> list[NormalizedXbrlFact]:
    computed_facts, _ = _compute_margin_with_diagnostics(
        metric_key=metric_key,
        numerator_key=numerator_key,
        facts_by_metric=facts_by_metric,
    )
    return computed_facts


def _compute_margin_with_diagnostics(
    *,
    metric_key: str,
    numerator_key: str,
    facts_by_metric: dict[str, list[NormalizedXbrlFact]],
) -> tuple[list[NormalizedXbrlFact], list[ComputedMetricDiagnostic]]:
    revenue_by_key = {
        _computed_match_key(fact): fact
        for fact in facts_by_metric.get("revenue", [])
    }
    computed: list[NormalizedXbrlFact] = []
    diagnostics: list[ComputedMetricDiagnostic] = []

    for numerator in facts_by_metric.get(numerator_key, []):
        key = _computed_match_key(numerator)
        revenue = revenue_by_key.get(key)
        if revenue is None:
            diagnostics.append(
                _build_computed_metric_diagnostic(
                    metric_key=metric_key,
                    reason="missing_matching_revenue",
                    message=(
                        f"Skipped {metric_key} for {_format_fact_period(numerator)} "
                        "because no revenue fact matched the same period and unit."
                    ),
                    source_facts=[numerator],
                )
            )
            continue
        if revenue.value <= 0:
            diagnostics.append(
                _build_computed_metric_diagnostic(
                    metric_key=metric_key,
                    reason="non_positive_revenue",
                    message=(
                        f"Skipped {metric_key} for {_format_fact_period(numerator)} "
                        f"because revenue was {revenue.value}."
                    ),
                    source_facts=[numerator, revenue],
                )
            )
            continue

        value = (numerator.value / revenue.value).quantize(
            RATIO_PRECISION,
            rounding=ROUND_HALF_UP,
        )
        computed.append(
            _build_computed_fact(
                metric_key=metric_key,
                value=value,
                unit=RATIO_UNIT,
                source_facts=[numerator, revenue],
                calculation_notes=(
                    f"Computed as {numerator_key} divided by revenue from source facts "
                    f"{numerator.source_fact_id} and {revenue.source_fact_id}."
                ),
            )
        )

    return computed, diagnostics


def _build_computed_metric_diagnostic(
    *,
    metric_key: str,
    reason: str,
    message: str,
    source_facts: list[NormalizedXbrlFact],
) -> ComputedMetricDiagnostic:
    anchor = source_facts[0] if source_facts else None
    return ComputedMetricDiagnostic(
        canonical_metric_key=metric_key,
        reason=reason,
        message=message,
        period_start=anchor.period_start if anchor is not None else None,
        period_end=anchor.period_end if anchor is not None else None,
        source_fiscal_year=anchor.source_fiscal_year if anchor is not None else None,
        fact_fiscal_year=anchor.fact_fiscal_year if anchor is not None else None,
        fiscal_period=anchor.fiscal_period if anchor is not None else None,
        unit=anchor.unit if anchor is not None else None,
        source_accession_number=(
            anchor.source_accession_number if anchor is not None else None
        ),
        source_fact_ids=tuple(fact.source_fact_id for fact in source_facts),
    )


def _computed_match_key(fact: NormalizedXbrlFact) -> tuple:
    return (
        fact.period_start,
        fact.period_end,
        fact.unit,
    )


def _build_computed_fact(
    *,
    metric_key: str,
    value: Decimal,
    unit: str,
    source_facts: list[NormalizedXbrlFact],
    calculation_notes: str,
) -> NormalizedXbrlFact:
    anchor = source_facts[0]
    source_fact_id = _build_computed_source_fact_id(metric_key, source_facts)
    return NormalizedXbrlFact(
        canonical_metric_key=metric_key,
        taxonomy_tag=COMPUTED_TAXONOMY_TAGS[metric_key],
        label=METRIC_LABELS[metric_key],
        period_start=anchor.period_start,
        period_end=anchor.period_end,
        source_fiscal_year=anchor.source_fiscal_year,
        fact_fiscal_year=anchor.fact_fiscal_year,
        fiscal_period=anchor.fiscal_period,
        form_type=anchor.form_type,
        filed_date=anchor.filed_date,
        unit=unit,
        value=value,
        source_accession_number=anchor.source_accession_number,
        source_fact_id=source_fact_id,
        is_computed=True,
        calculation_notes=calculation_notes,
        tag_priority=0,
    )


def _format_fact_period(fact: NormalizedXbrlFact) -> str:
    if fact.fact_fiscal_year is not None and fact.fiscal_period:
        return (
            f"FY{fact.fact_fiscal_year} {fact.fiscal_period} "
            f"ending {fact.period_end.isoformat()}"
        )
    if fact.period_start is not None:
        return f"period {fact.period_start.isoformat()} to {fact.period_end.isoformat()}"
    return f"period ending {fact.period_end.isoformat()}"


def _build_source_fact_id(
    *,
    metric_key: str,
    taxonomy_tag: str,
    unit: str,
    accession_number: str | None,
    period_start: date | None,
    period_end: date,
    source_fiscal_year: int | None,
    fiscal_period: str | None,
    filed_date: date | None,
    value: Decimal,
) -> str:
    return ":".join(
        [
            "sec-companyfacts",
            metric_key,
            taxonomy_tag,
            unit,
            accession_number or "no-accn",
            period_start.isoformat() if period_start is not None else "instant",
            period_end.isoformat(),
            str(source_fiscal_year) if source_fiscal_year is not None else "no-fy",
            fiscal_period or "no-fp",
            filed_date.isoformat() if filed_date is not None else "no-filed",
            format(value, "f"),
        ]
    )


def _build_computed_source_fact_id(
    metric_key: str,
    source_facts: list[NormalizedXbrlFact],
) -> str:
    digest = sha256(
        "|".join(fact.source_fact_id for fact in source_facts).encode("utf-8")
    ).hexdigest()[:32]
    return f"computed:{metric_key}:{digest}"


def summarize_skipped_facts(skipped_facts: list[SkippedXbrlFact]) -> dict[str, int]:
    summary: dict[str, int] = {}
    for skipped_fact in skipped_facts:
        summary[skipped_fact.reason] = summary.get(skipped_fact.reason, 0) + 1
    return summary


def summarize_computed_metric_diagnostics(
    diagnostics: list[ComputedMetricDiagnostic],
) -> dict[str, int]:
    summary: dict[str, int] = {}
    for diagnostic in diagnostics:
        summary[diagnostic.reason] = summary.get(diagnostic.reason, 0) + 1
    return summary


def serialize_skipped_fact_samples(
    skipped_facts: list[SkippedXbrlFact],
    *,
    limit: int = 20,
) -> list[dict[str, Any]]:
    return [
        {
            "canonical_metric_key": skipped_fact.canonical_metric_key,
            "taxonomy_tag": skipped_fact.taxonomy_tag,
            "reason": skipped_fact.reason,
            "source_accession_number": skipped_fact.source_accession_number,
            "form_type": skipped_fact.form_type,
            "period_start": (
                skipped_fact.period_start.isoformat()
                if skipped_fact.period_start is not None
                else None
            ),
            "period_end": (
                skipped_fact.period_end.isoformat()
                if skipped_fact.period_end is not None
                else None
            ),
            "source_fiscal_year": skipped_fact.source_fiscal_year,
            "fact_fiscal_year": skipped_fact.fact_fiscal_year,
            "fiscal_period": skipped_fact.fiscal_period,
        }
        for skipped_fact in skipped_facts[:limit]
    ]


def serialize_computed_metric_diagnostic_samples(
    diagnostics: list[ComputedMetricDiagnostic],
    *,
    limit: int = 20,
) -> list[dict[str, Any]]:
    return [
        {
            "canonical_metric_key": diagnostic.canonical_metric_key,
            "reason": diagnostic.reason,
            "message": diagnostic.message,
            "source_accession_number": diagnostic.source_accession_number,
            "period_start": (
                diagnostic.period_start.isoformat()
                if diagnostic.period_start is not None
                else None
            ),
            "period_end": (
                diagnostic.period_end.isoformat()
                if diagnostic.period_end is not None
                else None
            ),
            "source_fiscal_year": diagnostic.source_fiscal_year,
            "fact_fiscal_year": diagnostic.fact_fiscal_year,
            "fiscal_period": diagnostic.fiscal_period,
            "unit": diagnostic.unit,
            "source_fact_ids": list(diagnostic.source_fact_ids),
        }
        for diagnostic in diagnostics[:limit]
    ]


def _parse_date(value: Any) -> date | None:
    if value is None or not str(value).strip():
        return None

    try:
        return date.fromisoformat(str(value).strip())
    except ValueError:
        return None


def _parse_decimal(value: Any) -> Decimal | None:
    if value is None or not str(value).strip():
        return None

    try:
        return Decimal(str(value).strip())
    except InvalidOperation:
        return None


def _parse_int(value: Any) -> int | None:
    if value is None or not str(value).strip():
        return None

    try:
        return int(str(value).strip())
    except ValueError:
        return None


def _parse_optional_string(value: Any) -> str | None:
    if value is None or not str(value).strip():
        return None

    return str(value).strip()

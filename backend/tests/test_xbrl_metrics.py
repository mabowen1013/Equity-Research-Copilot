from datetime import UTC, date, datetime
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.models import Company, Filing, FinancialFact, Job
from app.services import (
    XBRL_METRICS_JOB_TYPE,
    XbrlCompanyNotFoundError,
    XbrlMetricsJobNotFoundError,
    XbrlMetricsService,
    build_sec_company_facts_url,
    normalize_company_facts,
    normalize_company_facts_with_diagnostics,
    summarize_computed_metric_diagnostics,
    summarize_skipped_facts,
)

NOW = datetime(2026, 5, 16, 12, 0, tzinfo=UTC)


def fact(
    val: int | float,
    *,
    accn: str = "0000320193-24-000123",
    start: str = "2023-10-01",
    end: str = "2024-09-28",
    fy: int = 2024,
    fp: str = "FY",
    form: str = "10-K",
    filed: str = "2024-11-01",
) -> dict:
    return {
        "start": start,
        "end": end,
        "val": val,
        "accn": accn,
        "fy": fy,
        "fp": fp,
        "form": form,
        "filed": filed,
    }


def company_facts_payload(*, capex: int = 80, revenue: int = 1000) -> dict:
    return {
        "facts": {
            "us-gaap": {
                "RevenueFromContractWithCustomerExcludingAssessedTax": {
                    "label": "Revenue from Contract",
                    "units": {"USD": [fact(revenue)]},
                },
                "Revenues": {
                    "label": "Revenues",
                    "units": {"USD": [fact(2000, filed="2024-11-02")]},
                },
                "GrossProfit": {
                    "label": "Gross Profit",
                    "units": {"USD": [fact(400)]},
                },
                "OperatingIncomeLoss": {
                    "label": "Operating Income",
                    "units": {"USD": [fact(250)]},
                },
                "NetIncomeLoss": {
                    "label": "Net Income",
                    "units": {
                        "USD": [
                            fact(90, filed="2024-10-31"),
                            fact(100, filed="2024-11-01"),
                        ]
                    },
                },
                "NetCashProvidedByUsedInOperatingActivities": {
                    "label": "Operating Cash Flow",
                    "units": {"USD": [fact(300)]},
                },
                "PaymentsToAcquirePropertyPlantAndEquipment": {
                    "label": "Capital Expenditures",
                    "units": {"USD": [fact(capex)]},
                },
            }
        }
    }


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
        job: Job | None = None,
        filings: list[Filing] | None = None,
    ) -> None:
        self.company = company
        self.job = job
        self.filings = filings or []
        self.added: list = []
        self.execute_calls: list[str] = []
        self.flush_calls = 0
        self.commit_calls = 0
        self.rollback_calls = 0
        self.next_id = 1

    def add(self, instance) -> None:
        if getattr(instance, "id", None) is None:
            instance.id = self.next_id
            self.next_id += 1

        if isinstance(instance, Job):
            self.job = instance
        self.added.append(instance)

    def execute(self, statement) -> None:
        self.execute_calls.append(str(statement))

    def flush(self) -> None:
        self.flush_calls += 1

    def get(self, model, instance_id: int):
        if model is Job:
            return self.job if self.job and self.job.id == instance_id else None
        if model is Company:
            return self.company if self.company and self.company.id == instance_id else None
        return None

    def scalar(self, statement):
        return self.company

    def scalars(self, statement) -> FakeScalarResult:
        return FakeScalarResult(self.filings)

    def commit(self) -> None:
        self.commit_calls += 1

    def rollback(self) -> None:
        self.rollback_calls += 1


class FakeCacheService:
    def __init__(self, payload: dict) -> None:
        self.payload = payload
        self.calls: list[dict] = []

    def get_or_fetch_json(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(response_json=self.payload)


def make_company() -> Company:
    return Company(id=42, ticker="AAPL", cik="0000320193", name="Apple Inc.")


def make_filing() -> Filing:
    return Filing(
        id=99,
        company_id=42,
        accession_number="0000320193-24-000123",
        form_type="10-K",
        filing_date=date(2024, 11, 1),
        report_date=date(2024, 9, 28),
        primary_document="aapl.htm",
        sec_filing_url="https://www.sec.gov/Archives/aapl-index.htm",
        sec_primary_document_url="https://www.sec.gov/Archives/aapl.htm",
    )


def metric_map(facts) -> dict[str, list]:
    result: dict[str, list] = {}
    for normalized_fact in facts:
        result.setdefault(normalized_fact.canonical_metric_key, []).append(normalized_fact)
    return result


def test_build_sec_company_facts_url_zero_pads_cik() -> None:
    assert build_sec_company_facts_url("320193").endswith("/CIK0000320193.json")


def test_normalize_company_facts_selects_core_metrics_and_computes_ratios() -> None:
    facts = normalize_company_facts(company_facts_payload())
    metrics = metric_map(facts)

    assert set(metrics) == {
        "revenue",
        "gross_profit",
        "operating_income",
        "net_income",
        "operating_cash_flow",
        "capital_expenditures",
        "free_cash_flow",
        "gross_margin",
        "operating_margin",
        "net_margin",
    }
    assert metrics["revenue"][0].value == Decimal("1000")
    assert metrics["revenue"][0].taxonomy_tag.endswith(
        "RevenueFromContractWithCustomerExcludingAssessedTax"
    )
    assert metrics["net_income"][0].value == Decimal("100")
    assert metrics["free_cash_flow"][0].value == Decimal("220")
    assert metrics["gross_margin"][0].value == Decimal("0.400000")
    assert metrics["operating_margin"][0].value == Decimal("0.250000")
    assert metrics["net_margin"][0].value == Decimal("0.100000")
    assert metrics["free_cash_flow"][0].is_computed is True
    assert metrics["free_cash_flow"][0].unit == "USD"
    assert metrics["gross_margin"][0].unit == "ratio"


def test_normalize_company_facts_uses_productive_assets_as_capex_fallback() -> None:
    payload = company_facts_payload()
    payload["facts"]["us-gaap"].pop("PaymentsToAcquirePropertyPlantAndEquipment")
    payload["facts"]["us-gaap"]["PaymentsToAcquireProductiveAssets"] = {
        "label": "Payments to Acquire Productive Assets",
        "units": {"USD": [fact(90)]},
    }

    result = normalize_company_facts_with_diagnostics(payload)
    metrics = metric_map(result.facts)

    assert metrics["capital_expenditures"][0].value == Decimal("90")
    assert metrics["capital_expenditures"][0].taxonomy_tag.endswith(
        "PaymentsToAcquireProductiveAssets"
    )
    assert metrics["free_cash_flow"][0].value == Decimal("210")


def test_normalize_company_facts_prefers_property_plant_equipment_capex_tag() -> None:
    payload = company_facts_payload(capex=80)
    payload["facts"]["us-gaap"]["PaymentsToAcquireProductiveAssets"] = {
        "label": "Payments to Acquire Productive Assets",
        "units": {"USD": [fact(90, filed="2024-11-02")]},
    }

    result = normalize_company_facts_with_diagnostics(payload)
    metrics = metric_map(result.facts)

    assert metrics["capital_expenditures"][0].value == Decimal("80")
    assert metrics["capital_expenditures"][0].taxonomy_tag.endswith(
        "PaymentsToAcquirePropertyPlantAndEquipment"
    )
    assert metrics["free_cash_flow"][0].value == Decimal("220")


def test_normalize_company_facts_skips_computed_metrics_when_inputs_are_unsafe() -> None:
    result = normalize_company_facts_with_diagnostics(
        company_facts_payload(capex=-80, revenue=0)
    )
    metrics = metric_map(result.facts)
    summary = summarize_computed_metric_diagnostics(result.computed_metric_diagnostics)

    assert "free_cash_flow" not in metrics
    assert "gross_margin" not in metrics
    assert "operating_margin" not in metrics
    assert "net_margin" not in metrics
    assert summary == {
        "negative_capex_value": 1,
        "non_positive_revenue": 3,
    }
    assert result.computed_metric_diagnostics[0].canonical_metric_key == "free_cash_flow"
    assert "capex value was negative" in result.computed_metric_diagnostics[0].message


def test_normalize_company_facts_computes_margins_across_comparative_accessions() -> None:
    payload = company_facts_payload()
    payload["facts"]["us-gaap"]["GrossProfit"]["units"]["USD"][0]["accn"] = (
        "0000320193-24-000456"
    )
    payload["facts"]["us-gaap"]["GrossProfit"]["units"]["USD"][0]["fy"] = 2025

    result = normalize_company_facts_with_diagnostics(payload)
    metrics = metric_map(result.facts)
    summary = summarize_computed_metric_diagnostics(result.computed_metric_diagnostics)

    assert metrics["gross_margin"][0].value == Decimal("0.400000")
    assert "0000320193-24-000456" in (metrics["gross_margin"][0].calculation_notes or "")
    assert "0000320193-24-000123" in (metrics["gross_margin"][0].calculation_notes or "")
    assert summary == {}


def test_normalize_company_facts_deduplicates_comparative_filing_facts_by_period() -> None:
    payload = {
        "facts": {
            "us-gaap": {
                "PaymentsToAcquirePropertyPlantAndEquipment": {
                    "label": "Capital Expenditures",
                    "units": {
                        "USD": [
                            fact(
                                3790000000,
                                accn="0000320193-23-000006",
                                start="2022-09-25",
                                end="2022-12-31",
                                fy=2023,
                                fp="Q1",
                                form="10-Q",
                                filed="2023-02-03",
                            ),
                            fact(
                                3790000000,
                                accn="0000320193-24-000006",
                                start="2022-09-25",
                                end="2022-12-31",
                                fy=2024,
                                fp="Q1",
                                form="10-Q",
                                filed="2024-02-02",
                            ),
                        ]
                    },
                },
            },
        },
    }

    facts = normalize_company_facts(payload)

    assert len(facts) == 1
    assert facts[0].canonical_metric_key == "capital_expenditures"
    assert facts[0].period_start == date(2022, 9, 25)
    assert facts[0].period_end == date(2022, 12, 31)
    assert facts[0].fiscal_year == 2024
    assert facts[0].source_accession_number == "0000320193-24-000006"


def test_normalize_company_facts_records_skipped_fact_reasons() -> None:
    payload = company_facts_payload()
    gross_profit_facts = payload["facts"]["us-gaap"]["GrossProfit"]["units"]["USD"]
    gross_profit_facts.extend(
        [
            fact(401, form="4"),
            fact(402) | {"segment": {"dimension": "product"}},
            fact(403, start="not-a-date"),
            fact("not-a-number"),
        ]
    )

    result = normalize_company_facts_with_diagnostics(payload)
    summary = summarize_skipped_facts(result.skipped_facts)

    assert summary == {
        "unsupported_form": 1,
        "segmented_fact": 1,
        "invalid_period": 1,
        "invalid_value": 1,
    }
    assert len(result.facts) == 10
    assert result.skipped_facts[0].canonical_metric_key == "gross_profit"
    assert result.skipped_facts[0].taxonomy_tag == "us-gaap:GrossProfit"


def test_replace_company_metrics_deletes_old_facts_and_links_source_filing() -> None:
    company = make_company()
    session = FakeSession(company=company, filings=[make_filing()])
    service = XbrlMetricsService(
        session,
        cache_service=FakeCacheService(company_facts_payload()),
    )
    normalized_facts = normalize_company_facts(company_facts_payload())

    result = service.replace_company_metrics(company, normalized_facts)

    assert any("DELETE FROM financial_facts" in call for call in session.execute_calls)
    assert result.stored_facts_count == 10
    assert result.computed_facts_count == 4
    assert result.missing_metrics == []
    assert result.computed_diagnostics_count == 0
    assert result.computed_diagnostic_reasons == {}
    stored_fact = next(item for item in session.added if isinstance(item, FinancialFact))
    assert stored_fact.source_filing_id == 99
    assert stored_fact.source_filing_url.endswith("aapl-index.htm")


def test_create_job_requires_existing_company() -> None:
    service = XbrlMetricsService(
        FakeSession(company=None),
        cache_service=FakeCacheService(company_facts_payload()),
    )

    with pytest.raises(XbrlCompanyNotFoundError):
        service.create_job("AAPL")


def test_run_job_loads_metrics_and_marks_succeeded() -> None:
    company = make_company()
    payload = company_facts_payload()
    payload["facts"]["us-gaap"]["GrossProfit"]["units"]["USD"].append(fact(401, form="4"))
    job = Job(
        id=77,
        job_type=XBRL_METRICS_JOB_TYPE,
        company_id=42,
        status="pending",
        progress=0,
        retry_count=0,
        payload={"company_id": 42, "ticker": "AAPL", "refresh": True, "stage": "queued"},
        error_message=None,
        created_at=NOW,
        updated_at=NOW,
    )
    cache_service = FakeCacheService(payload)
    session = FakeSession(company=company, job=job, filings=[make_filing()])
    service = XbrlMetricsService(
        session,
        cache_service=cache_service,
        clock=lambda: NOW,
    )

    result = service.run_job(77)

    assert result.status == "succeeded"
    assert result.progress == 100
    assert result.payload["stage"] == "completed"
    assert result.payload["stored_facts_count"] == 10
    assert result.payload["computed_facts_count"] == 4
    assert result.payload["skipped_facts_count"] == 1
    assert result.payload["skipped_fact_reasons"] == {"unsupported_form": 1}
    assert result.payload["skipped_fact_samples"][0]["canonical_metric_key"] == "gross_profit"
    assert result.payload["computed_diagnostics_count"] == 0
    assert result.payload["computed_diagnostic_reasons"] == {}
    assert result.payload["computed_diagnostic_samples"] == []
    assert cache_service.calls[0]["refresh"] is True
    assert session.commit_calls == 4
    assert session.rollback_calls == 0


def test_run_job_raises_for_unknown_job_id() -> None:
    service = XbrlMetricsService(
        FakeSession(job=None),
        cache_service=FakeCacheService(company_facts_payload()),
    )

    with pytest.raises(XbrlMetricsJobNotFoundError):
        service.run_job(999)

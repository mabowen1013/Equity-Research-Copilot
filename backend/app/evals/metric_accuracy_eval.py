"""XBRL-grounded structured-metric accuracy, at scale.

Auto-generates questions of the form "What was <ticker>'s <metric> in fiscal year
<FY>?" from the financial_facts table (the SEC XBRL ground truth), runs the real
answer pipeline, and checks whether the answer states the true value within
tolerance. Generated programmatically over every company/metric/recent-FY (not
hand-picked), so it removes selection bias and gives a large n with a Wilson 95%
confidence interval.

Only the latest few fiscal years are used: older XBRL facts carry occasional
mis-tagged/duplicate values, so recent annual figures are the trustworthy truth.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass, field
import json
import math
from pathlib import Path
from typing import Any, Iterable, Protocol

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_sessionmaker
from app.models import Company, FinancialFact
from app.schemas import RetrievalRequest
from app.schemas.answer import ResearchAnswerResponseRead
from app.services import ResearchAnswerService
from decimal import Decimal

from app.services.answer_generation import parse_salient_numbers

DEFAULT_EVAL_FILE = "backend/evals/metric_accuracy_eval.json"
DEFAULT_FISCAL_YEARS = 3
DEFAULT_REL_TOL = 0.03

AMOUNT_METRICS: dict[str, str] = {
    "revenue": "total revenue",
    "net_income": "net income",
    "operating_income": "operating income",
    "gross_profit": "gross profit",
    "operating_cash_flow": "operating cash flow",
    "free_cash_flow": "free cash flow",
}


class AnswerService(Protocol):
    def answer(self, request: RetrievalRequest) -> ResearchAnswerResponseRead:
        """Run retrieval + answer generation for a request."""


def generate_cases(
    db: Session,
    *,
    fiscal_years: int = DEFAULT_FISCAL_YEARS,
    metrics: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    metrics = metrics or AMOUNT_METRICS
    rows = db.execute(
        select(
            Company.ticker,
            FinancialFact.canonical_metric_key,
            FinancialFact.fact_fiscal_year,
            FinancialFact.period_end,
            FinancialFact.filed_date,
            FinancialFact.value,
        )
        .join(Company, Company.id == FinancialFact.company_id)
        .where(
            FinancialFact.fiscal_period == "FY",
            FinancialFact.canonical_metric_key.in_(list(metrics)),
            FinancialFact.fact_fiscal_year.is_not(None),
        )
        .order_by(
            Company.ticker,
            FinancialFact.canonical_metric_key,
            FinancialFact.fact_fiscal_year.desc(),
            FinancialFact.filed_date.desc().nullslast(),
            FinancialFact.id.desc(),
        )
    ).all()

    # Dedupe to one value per (ticker, metric, fiscal_year): the row above is the
    # latest-filed for that year. Then keep the latest `fiscal_years` years.
    seen: set[tuple[str, str, int]] = set()
    per_group: dict[tuple[str, str], int] = {}
    cases: list[dict[str, Any]] = []
    for ticker, metric, fy, _period_end, _filed, value in rows:
        key = (ticker, metric, fy)
        if key in seen:
            continue
        seen.add(key)
        group = (ticker, metric)
        if per_group.get(group, 0) >= fiscal_years:
            continue
        per_group[group] = per_group.get(group, 0) + 1
        cases.append(
            {
                "id": f"{ticker}_{metric}_FY{fy}",
                "ticker": ticker,
                "metric": metric,
                "fiscal_year": fy,
                "question": f"What was {ticker}'s {metrics[metric]} in fiscal year {fy}?",
                "kind": "amount",
                "value": float(value),
                "rel_tol": DEFAULT_REL_TOL,
            }
        )
    return cases


@dataclass(frozen=True)
class MetricAccuracyCaseResult:
    case_id: str
    ticker: str
    metric: str
    fiscal_year: int
    expected_value: float
    status: str  # "correct" | "wrong" | "declined"
    answer_excerpt: str = ""


@dataclass(frozen=True)
class MetricAccuracyEvalResult:
    suite_name: str
    eval_file: Path
    results: list[MetricAccuracyCaseResult] = field(default_factory=list)

    @property
    def total(self) -> int:
        return len(self.results)

    @property
    def correct(self) -> int:
        return sum(1 for r in self.results if r.status == "correct")

    @property
    def declined(self) -> int:
        return sum(1 for r in self.results if r.status == "declined")

    @property
    def answered(self) -> int:
        return sum(1 for r in self.results if r.status in ("correct", "wrong"))

    @property
    def accuracy(self) -> float:
        return self.correct / self.total if self.total else 0.0

    @property
    def accuracy_when_answered(self) -> float:
        return self.correct / self.answered if self.answered else 0.0

    @property
    def wilson_ci(self) -> tuple[float, float]:
        return wilson_interval(self.correct, self.total)


def wilson_interval(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    if n == 0:
        return (0.0, 0.0)
    p = k / n
    denom = 1 + z * z / n
    center = (p + z * z / (2 * n)) / denom
    half = (z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))) / denom
    return (max(0.0, center - half), min(1.0, center + half))


def run_eval_file(
    eval_file: str | Path = DEFAULT_EVAL_FILE,
    *,
    db: Session | None = None,
    answer_service: AnswerService | None = None,
    limit: int | None = None,
) -> MetricAccuracyEvalResult:
    path = Path(eval_file)
    data = json.loads(path.read_text())
    cases = data.get("cases", [])
    if limit is not None:
        cases = cases[:limit]

    owns_session = db is None and answer_service is None
    session = db or (get_sessionmaker()() if answer_service is None else None)
    active = answer_service or ResearchAnswerService(session)

    try:
        results = [evaluate_case(case, active) for case in cases]
    finally:
        if owns_session and session is not None:
            session.close()

    return MetricAccuracyEvalResult(
        suite_name=data.get("suite_name", path.stem),
        eval_file=path,
        results=results,
    )


def evaluate_case(case: dict[str, Any], answer_service: AnswerService) -> MetricAccuracyCaseResult:
    request = RetrievalRequest(ticker=str(case["ticker"]), question=str(case["question"]))
    response = answer_service.answer(request)
    answer = response.answer or ""
    if response.validation_status == "insufficient_evidence":
        status = "declined"
    else:
        status = (
            "correct"
            if primary_amount_matches(
                answer, Decimal(str(case["value"])), Decimal(str(case.get("rel_tol", DEFAULT_REL_TOL)))
            )
            else "wrong"
        )
    return MetricAccuracyCaseResult(
        case_id=case.get("id", f"{request.ticker}:{request.question}"),
        ticker=str(case["ticker"]),
        metric=str(case.get("metric", "")),
        fiscal_year=int(case.get("fiscal_year", 0)),
        expected_value=float(case["value"]),
        status=status,
        answer_excerpt=" ".join(answer.split())[:160],
    )


def primary_amount_matches(answer: str, expected: Decimal, rel_tol: Decimal) -> bool:
    """Compare the answer's PRIMARY (first) dollar figure to the XBRL truth.

    Using the first amount, not "any amount in the text", is deliberate: answers
    carry year-over-year context, so a prior-year figure also appears; matching
    "any" would mask period-confusion errors (stating the wrong year's value as
    the headline figure).
    """
    amounts = [value for kind, value, _ in parse_salient_numbers(answer) if kind == "amount"]
    if not amounts:
        return False
    primary = amounts[0]
    diff = abs(primary - expected)
    largest = max(abs(primary), abs(expected))
    return largest > 0 and diff <= rel_tol * largest


def format_eval_result(result: MetricAccuracyEvalResult, *, max_show: int = 25) -> str:
    lo, hi = result.wilson_ci
    lines = [
        f"Metric Accuracy Eval: {result.suite_name}",
        f"file: {result.eval_file}",
        f"cases: {result.total}",
        f"accuracy: {result.accuracy:.1%}  (95% CI {lo:.1%}-{hi:.1%})",
        f"accuracy when answered: {result.accuracy_when_answered:.1%}  (answered {result.answered}/{result.total})",
        f"declined: {result.declined}",
    ]
    wrong = [r for r in result.results if r.status != "correct"]
    if wrong:
        lines.append("")
        lines.append("Non-correct cases:")
        for r in wrong[:max_show]:
            lines.append(
                f"- [{r.status}] {r.case_id} expected={r.expected_value:,.0f} :: {r.answer_excerpt}"
            )
        if len(wrong) > max_show:
            lines.append(f"... {len(wrong) - max_show} more")
    return "\n".join(lines)


def _json_result(result: MetricAccuracyEvalResult) -> dict[str, Any]:
    lo, hi = result.wilson_ci
    return {
        "suite_name": result.suite_name,
        "cases": result.total,
        "accuracy": result.accuracy,
        "accuracy_ci95": [lo, hi],
        "accuracy_when_answered": result.accuracy_when_answered,
        "answered": result.answered,
        "declined": result.declined,
        "results": [
            {
                "id": r.case_id,
                "metric": r.metric,
                "fiscal_year": r.fiscal_year,
                "expected_value": r.expected_value,
                "status": r.status,
            }
            for r in result.results
        ],
    }


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="XBRL-grounded structured-metric accuracy eval.")
    parser.add_argument("eval_file", nargs="?", default=DEFAULT_EVAL_FILE)
    parser.add_argument("--generate", metavar="PATH", help="Generate the case file from the DB and exit.")
    parser.add_argument("--fiscal-years", type=int, default=DEFAULT_FISCAL_YEARS)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--json", action="store_true", dest="json_output")
    parser.add_argument("--no-fail-on-mismatch", action="store_true")
    return parser


def main(argv: Iterable[str] | None = None) -> int:
    args = _build_parser().parse_args(list(argv) if argv is not None else None)

    if args.generate:
        with get_sessionmaker()() as db:
            cases = generate_cases(db, fiscal_years=args.fiscal_years)
        Path(args.generate).write_text(
            json.dumps(
                {
                    "suite_name": "metric_accuracy_eval",
                    "description": (
                        "Auto-generated from financial_facts: latest "
                        f"{args.fiscal_years} fiscal years per company/metric. Ground "
                        "truth is the SEC XBRL value; answer must state it within "
                        f"{DEFAULT_REL_TOL:.0%}."
                    ),
                    "cases": cases,
                },
                indent=2,
            )
        )
        print(f"Wrote {len(cases)} cases to {args.generate}")
        return 0

    result = run_eval_file(args.eval_file, limit=args.limit)
    if args.json_output:
        print(json.dumps(_json_result(result), indent=2, ensure_ascii=False))
    else:
        print(format_eval_result(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

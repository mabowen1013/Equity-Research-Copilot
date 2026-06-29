from __future__ import annotations

import argparse
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
import json
from pathlib import Path
import re
from typing import Any, Iterable, Protocol

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core import Settings
from app.db import get_sessionmaker
from app.models import Company, Filing
from app.schemas import RetrievalRequest
from app.schemas.answer import CitationValidationRead
from app.schemas.research_run import ResearchRunRead
from app.services import ResearchRunService
from app.services.answer_generation import parse_salient_numbers

DEFAULT_AMOUNT_REL_TOL = Decimal("0.02")
DEFAULT_PERCENT_ABS_TOL = Decimal("0.3")
GROUNDING_WARNING_CODES = ("unsupported_number", "citation_number_mismatch")


DEFAULT_EVAL_FILE = "backend/evals/answer_gold_eval.json"

# Answers must never read as investment advice regardless of the question.
DEFAULT_FORBIDDEN_PATTERNS = [
    r"(?i)\bprice target\b",
    r"(?i)\bwe recommend\b",
    r"(?i)\b(buy|sell|hold)\s+(rating|recommendation)\b",
]


class ResearchRunner(Protocol):
    def run(self, request: RetrievalRequest) -> ResearchRunRead:
        """Execute a research run and return the packaged result."""


@dataclass(frozen=True)
class AnswerCheckFailure:
    code: str
    detail: str


@dataclass(frozen=True)
class AnswerGoldCaseResult:
    case_id: str
    ticker: str
    question: str
    validation_status: str
    citation_count: int
    claim_sentence_count: int
    cited_claim_sentence_count: int
    duration_ms: float
    contradicted_claims: int = 0
    unsupported_claims: int = 0
    grounding_warnings: int = 0
    failures: list[AnswerCheckFailure] = field(default_factory=list)

    @property
    def claim_citation_coverage(self) -> float:
        if not self.claim_sentence_count:
            return 1.0
        return self.cited_claim_sentence_count / self.claim_sentence_count

    @property
    def passed(self) -> bool:
        return not self.failures


@dataclass(frozen=True)
class AnswerGoldEvalResult:
    suite_name: str
    eval_file: Path
    results: list[AnswerGoldCaseResult]

    @property
    def passed_count(self) -> int:
        return sum(1 for result in self.results if result.passed)

    @property
    def failed_count(self) -> int:
        return len(self.results) - self.passed_count

    @property
    def pass_rate(self) -> float:
        if not self.results:
            return 0.0
        return self.passed_count / len(self.results)

    @property
    def mean_claim_citation_coverage(self) -> float:
        if not self.results:
            return 0.0
        return sum(result.claim_citation_coverage for result in self.results) / len(
            self.results
        )

    @property
    def mean_duration_ms(self) -> float:
        if not self.results:
            return 0.0
        return sum(result.duration_ms for result in self.results) / len(self.results)

    @property
    def total_contradicted_claims(self) -> int:
        return sum(result.contradicted_claims for result in self.results)

    @property
    def total_unsupported_claims(self) -> int:
        return sum(result.unsupported_claims for result in self.results)

    @property
    def total_grounding_warnings(self) -> int:
        return sum(result.grounding_warnings for result in self.results)


def run_eval_file(
    eval_file: str | Path = DEFAULT_EVAL_FILE,
    *,
    db: Session | None = None,
    runner: ResearchRunner | None = None,
    enable_entailment: bool = True,
    enable_relevance: bool = True,
) -> AnswerGoldEvalResult:
    path = Path(eval_file)
    data = json.loads(path.read_text())
    owns_session = db is None and runner is None
    session = db or (get_sessionmaker()() if runner is None else None)
    if runner is not None:
        active_runner: ResearchRunner = runner
    else:
        # Turn the safety gates on for eval so they are exercised; production keeps
        # them opt-in via env (extra LLM calls on the answer path).
        overrides: dict[str, bool] = {}
        if enable_entailment:
            overrides["answer_entailment_check"] = True
        if enable_relevance:
            overrides["answer_relevance_check"] = True
        settings = Settings(**overrides) if overrides else None
        active_runner = ResearchRunService(session, settings=settings)

    try:
        results = [
            evaluate_case(case, active_runner, db=session)
            for case in data.get("cases", [])
        ]
    finally:
        if owns_session and session is not None:
            session.close()

    return AnswerGoldEvalResult(
        suite_name=data.get("suite_name", path.stem),
        eval_file=path,
        results=results,
    )


def evaluate_case(
    case: dict[str, Any],
    runner: ResearchRunner,
    *,
    db: Session | None = None,
) -> AnswerGoldCaseResult:
    request = RetrievalRequest(
        ticker=str(case["ticker"]),
        question=str(case["question"]),
        form_type=case.get("form_type"),
        date_from=case.get("date_from"),
        date_to=case.get("date_to"),
        section=case.get("section"),
    )
    run = ResearchRunRead.model_validate(runner.run(request))
    validation = coerce_validation(run)
    counts = issue_counts(validation)
    latest_filing = None
    if db is not None and case.get("expect_latest_filing"):
        latest_filing = latest_filing_date(db, request.ticker, case.get("expect_form_types"))
    failures = check_case(case, run, latest_filing_date=latest_filing)
    return AnswerGoldCaseResult(
        case_id=case.get("id", f"{request.ticker}:{request.question}"),
        ticker=request.ticker,
        question=request.question,
        validation_status=run.validation_status,
        citation_count=len(run.citations),
        claim_sentence_count=validation.claim_sentence_count,
        cited_claim_sentence_count=validation.cited_claim_sentence_count,
        duration_ms=run.duration_ms or 0.0,
        contradicted_claims=counts.get("contradicted_claim", 0),
        unsupported_claims=counts.get("unsupported_claim", 0),
        grounding_warnings=sum(counts.get(code, 0) for code in GROUNDING_WARNING_CODES),
        failures=failures,
    )


def issue_counts(validation: CitationValidationRead) -> dict[str, int]:
    counts: dict[str, int] = {}
    for issue in [*validation.errors, *validation.warnings]:
        counts[issue.code] = counts.get(issue.code, 0) + 1
    return counts


def latest_filing_date(
    db: Session,
    ticker: str,
    form_types: list[str] | None = None,
) -> str | None:
    statement = (
        select(Filing.filing_date)
        .join(Company, Company.id == Filing.company_id)
        .where(Company.ticker == ticker.strip().upper())
        .order_by(Filing.filing_date.desc())
    )
    if form_types:
        statement = statement.where(
            Filing.form_type.in_([form.strip().upper() for form in form_types])
        )
    value = db.execute(statement.limit(1)).scalar()
    return value.isoformat() if value is not None else None


def coerce_validation(run: ResearchRunRead) -> CitationValidationRead:
    if isinstance(run.validation, CitationValidationRead):
        return run.validation
    return CitationValidationRead.model_validate(run.validation)


def check_case(
    case: dict[str, Any],
    run: ResearchRunRead,
    *,
    latest_filing_date: str | None = None,
) -> list[AnswerCheckFailure]:
    failures: list[AnswerCheckFailure] = []
    answer = run.answer or ""
    validation = coerce_validation(run)

    expected_status = str(case.get("expect_validation_status", "passed"))
    if run.validation_status != expected_status:
        failures.append(
            AnswerCheckFailure(
                code="validation_status",
                detail=f"expected {expected_status}, got {run.validation_status}",
            )
        )

    min_citations = int(case.get("min_citations", 1 if expected_status == "passed" else 0))
    if len(run.citations) < min_citations:
        failures.append(
            AnswerCheckFailure(
                code="min_citations",
                detail=f"expected >= {min_citations} citations, got {len(run.citations)}",
            )
        )

    for pattern in case.get("must_match", []):
        if not re.search(pattern, answer):
            failures.append(
                AnswerCheckFailure(
                    code="must_match",
                    detail=f"answer did not match: {pattern}",
                )
            )

    forbidden = [*DEFAULT_FORBIDDEN_PATTERNS, *case.get("must_not_match", [])]
    for pattern in forbidden:
        if re.search(pattern, answer):
            failures.append(
                AnswerCheckFailure(
                    code="must_not_match",
                    detail=f"answer matched forbidden pattern: {pattern}",
                )
            )

    min_coverage = case.get("min_claim_citation_coverage")
    if min_coverage is not None:
        claim_count = validation.claim_sentence_count
        coverage = (
            validation.cited_claim_sentence_count / claim_count
            if claim_count
            else 1.0
        )
        if coverage < float(min_coverage):
            failures.append(
                AnswerCheckFailure(
                    code="claim_citation_coverage",
                    detail=f"coverage {coverage:.1%} below required {float(min_coverage):.1%}",
                )
            )

    max_duration_ms = case.get("max_duration_ms")
    duration_ms = run.duration_ms or 0.0
    if max_duration_ms is not None and duration_ms > float(max_duration_ms):
        failures.append(
            AnswerCheckFailure(
                code="max_duration_ms",
                detail=f"run took {duration_ms:.0f}ms, budget {float(max_duration_ms):.0f}ms",
            )
        )

    # Numeric ground truth: the answer must state the SEC-XBRL value (within tol).
    answer_numbers = parse_salient_numbers(answer)
    for spec in case.get("expect_values", []):
        if not value_supported(spec, answer_numbers):
            failures.append(
                AnswerCheckFailure(
                    code="value_mismatch",
                    detail=(
                        f"answer is missing expected {spec.get('kind', 'amount')} "
                        f"value {spec.get('value')} (tol)"
                    ),
                )
            )

    # Period correctness: the newest cited filing must be the latest one.
    if case.get("expect_latest_filing") and latest_filing_date is not None:
        cited_dates = [c.filing_date for c in run.citations if c.filing_date]
        newest_cited = max(cited_dates) if cited_dates else None
        if newest_cited != latest_filing_date:
            failures.append(
                AnswerCheckFailure(
                    code="stale_filing",
                    detail=f"newest cited filing {newest_cited} != latest {latest_filing_date}",
                )
            )

    # Grounding signals are surfaced as metrics on every case and can be gated
    # per case via max_warning_codes. They are NOT a hard default gate: the
    # numeric-grounding check has known false positives on *derived* figures
    # (e.g. a growth "65%" that is computed, not literally present in evidence),
    # so a blanket cap of 0 would fail correct answers. The hard faithfulness
    # gate is the contradicted-claim check below.
    counts = issue_counts(validation)
    for code, cap in case.get("max_warning_codes", {}).items():
        if counts.get(code, 0) > int(cap):
            failures.append(
                AnswerCheckFailure(
                    code="warning_cap",
                    detail=f"{code}={counts.get(code, 0)} exceeds cap {cap}",
                )
            )

    max_contradicted = int(case.get("max_contradicted_claims", 0))
    if counts.get("contradicted_claim", 0) > max_contradicted:
        failures.append(
            AnswerCheckFailure(
                code="contradicted_claim",
                detail=f"{counts.get('contradicted_claim', 0)} contradicted claim(s) > {max_contradicted}",
            )
        )

    return failures


def value_supported(
    spec: dict[str, Any],
    numbers: list[tuple[str, Decimal, str]],
) -> bool:
    kind = str(spec.get("kind", "amount")).lower()
    try:
        expected = Decimal(str(spec.get("value")))
    except (InvalidOperation, TypeError):
        return False
    rel_tol = Decimal(str(spec.get("rel_tol", DEFAULT_AMOUNT_REL_TOL)))
    abs_tol_raw = spec.get("abs_tol")
    abs_tol = (
        Decimal(str(abs_tol_raw))
        if abs_tol_raw is not None
        else (DEFAULT_PERCENT_ABS_TOL if kind == "percent" else Decimal(0))
    )
    for number_kind, number_value, _ in numbers:
        if number_kind != kind:
            continue
        diff = abs(number_value - expected)
        if diff <= abs_tol:
            return True
        largest = max(abs(number_value), abs(expected))
        if largest > 0 and diff <= rel_tol * largest:
            return True
    return False


def format_eval_result(
    result: AnswerGoldEvalResult,
    *,
    max_failures: int = 20,
) -> str:
    lines = [
        f"Answer Gold Eval: {result.suite_name}",
        f"file: {result.eval_file}",
        f"cases: {len(result.results)}",
        f"passed: {result.passed_count}",
        f"failed: {result.failed_count}",
        f"pass_rate: {result.pass_rate:.1%}",
        f"mean_claim_citation_coverage: {result.mean_claim_citation_coverage:.1%}",
        f"mean_duration_ms: {result.mean_duration_ms:.0f}",
        f"contradicted_claims: {result.total_contradicted_claims}",
        f"unsupported_claims: {result.total_unsupported_claims}",
        f"grounding_warnings: {result.total_grounding_warnings}",
    ]

    failed_results = [case for case in result.results if not case.passed]
    if not failed_results:
        return "\n".join(lines)

    lines.append("")
    lines.append("Failures:")
    for case in failed_results[:max_failures]:
        lines.append(f"- {case.case_id}")
        lines.append(f"  query: {case.question}")
        for failure in case.failures:
            lines.append(f"  [{failure.code}] {failure.detail}")

    remaining = len(failed_results) - max_failures
    if remaining > 0:
        lines.append(f"... {remaining} more failure(s) omitted")
    return "\n".join(lines)


def _json_result(result: AnswerGoldEvalResult) -> dict[str, Any]:
    return {
        "suite_name": result.suite_name,
        "eval_file": str(result.eval_file),
        "cases": len(result.results),
        "passed": result.passed_count,
        "failed": result.failed_count,
        "pass_rate": result.pass_rate,
        "mean_claim_citation_coverage": result.mean_claim_citation_coverage,
        "mean_duration_ms": result.mean_duration_ms,
        "total_contradicted_claims": result.total_contradicted_claims,
        "total_unsupported_claims": result.total_unsupported_claims,
        "total_grounding_warnings": result.total_grounding_warnings,
        "results": [
            {
                "id": case.case_id,
                "ticker": case.ticker,
                "question": case.question,
                "passed": case.passed,
                "validation_status": case.validation_status,
                "citation_count": case.citation_count,
                "claim_citation_coverage": case.claim_citation_coverage,
                "duration_ms": case.duration_ms,
                "failures": [
                    {"code": failure.code, "detail": failure.detail}
                    for failure in case.failures
                ],
            }
            for case in result.results
        ],
    }


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run end-to-end answer quality evals against the research-run API.",
    )
    parser.add_argument(
        "eval_file",
        nargs="?",
        default=DEFAULT_EVAL_FILE,
        help=f"Path to eval JSON file. Defaults to {DEFAULT_EVAL_FILE}.",
    )
    parser.add_argument(
        "--max-failures",
        type=int,
        default=20,
        help="Maximum number of failed cases to print.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Print machine-readable JSON output.",
    )
    parser.add_argument(
        "--no-fail-on-mismatch",
        action="store_true",
        help="Exit 0 even when eval cases fail.",
    )
    return parser


def main(argv: Iterable[str] | None = None) -> int:
    args = _build_parser().parse_args(list(argv) if argv is not None else None)
    result = run_eval_file(args.eval_file)

    if args.json_output:
        print(json.dumps(_json_result(result), indent=2, ensure_ascii=False))
    else:
        print(format_eval_result(result, max_failures=args.max_failures))

    if result.failed_count and not args.no_fail_on_mismatch:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

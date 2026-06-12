from __future__ import annotations

import argparse
from dataclasses import dataclass, field
import json
from pathlib import Path
import re
from typing import Any, Iterable, Protocol

from sqlalchemy.orm import Session

from app.db import get_sessionmaker
from app.schemas import RetrievalRequest
from app.schemas.answer import CitationValidationRead
from app.schemas.research_run import ResearchRunRead
from app.services import ResearchRunService


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


def run_eval_file(
    eval_file: str | Path = DEFAULT_EVAL_FILE,
    *,
    db: Session | None = None,
    runner: ResearchRunner | None = None,
) -> AnswerGoldEvalResult:
    path = Path(eval_file)
    data = json.loads(path.read_text())
    owns_session = db is None and runner is None
    session = db or (get_sessionmaker()() if runner is None else None)
    active_runner = runner or ResearchRunService(session)

    try:
        results = [
            evaluate_case(case, active_runner)
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
    failures = check_case(case, run)
    return AnswerGoldCaseResult(
        case_id=case.get("id", f"{request.ticker}:{request.question}"),
        ticker=request.ticker,
        question=request.question,
        validation_status=run.validation_status,
        citation_count=len(run.citations),
        claim_sentence_count=validation.claim_sentence_count,
        cited_claim_sentence_count=validation.cited_claim_sentence_count,
        duration_ms=run.duration_ms or 0.0,
        failures=failures,
    )


def coerce_validation(run: ResearchRunRead) -> CitationValidationRead:
    if isinstance(run.validation, CitationValidationRead):
        return run.validation
    return CitationValidationRead.model_validate(run.validation)


def check_case(case: dict[str, Any], run: ResearchRunRead) -> list[AnswerCheckFailure]:
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

    return failures


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

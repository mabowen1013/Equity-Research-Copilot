from __future__ import annotations

import argparse
from dataclasses import dataclass, field
import json
from pathlib import Path
from typing import Any, Iterable, Protocol

from sqlalchemy.orm import Session

from app.db import get_sessionmaker
from app.schemas import RetrievalRequest, RetrievalResponse
from app.services import RetrievalService, build_answer_evidence_context


DEFAULT_EVAL_FILE = "backend/evals/retrieval_gold_eval.json"


class Retriever(Protocol):
    def retrieve(self, request: RetrievalRequest) -> RetrievalResponse:
        """Return retrieval evidence for a request."""


@dataclass(frozen=True)
class MissingEvidence:
    evidence_id: str


@dataclass(frozen=True)
class RetrievalGoldCaseResult:
    case_id: str
    ticker: str
    question: str
    expected_evidence_ids: list[str]
    actual_evidence_ids: list[str]
    min_recall: float
    missing: list[MissingEvidence] = field(default_factory=list)

    @property
    def recall(self) -> float:
        if not self.expected_evidence_ids:
            return 1.0
        matched = len(self.expected_evidence_ids) - len(self.missing)
        return matched / len(self.expected_evidence_ids)

    @property
    def passed(self) -> bool:
        return self.recall >= self.min_recall


@dataclass(frozen=True)
class RetrievalGoldEvalResult:
    suite_name: str
    eval_file: Path
    results: list[RetrievalGoldCaseResult]

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


def run_eval_file(
    eval_file: str | Path = DEFAULT_EVAL_FILE,
    *,
    db: Session | None = None,
    retriever: Retriever | None = None,
) -> RetrievalGoldEvalResult:
    path = Path(eval_file)
    data = json.loads(path.read_text())
    owns_session = db is None and retriever is None
    session = db or (get_sessionmaker()() if retriever is None else None)
    active_retriever = retriever or RetrievalService(session)

    try:
        results = [
            evaluate_case(case, active_retriever)
            for case in data.get("cases", [])
        ]
    finally:
        if owns_session and session is not None:
            session.close()

    return RetrievalGoldEvalResult(
        suite_name=data.get("suite_name", path.stem),
        eval_file=path,
        results=results,
    )


def evaluate_case(
    case: dict[str, Any],
    retriever: Retriever,
) -> RetrievalGoldCaseResult:
    request = RetrievalRequest(
        ticker=str(case["ticker"]),
        question=str(case["question"]),
        form_type=case.get("form_type"),
        date_from=case.get("date_from"),
        date_to=case.get("date_to"),
        section=case.get("section"),
    )
    response = RetrievalResponse.model_validate(retriever.retrieve(request))
    context = build_answer_evidence_context(request, response)
    expected_ids = [str(evidence_id) for evidence_id in case.get("expected_evidence_ids", [])]
    actual_ids = context.allowed_evidence_ids
    actual_id_set = set(actual_ids)
    missing = [
        MissingEvidence(evidence_id=evidence_id)
        for evidence_id in expected_ids
        if evidence_id not in actual_id_set
    ]
    return RetrievalGoldCaseResult(
        case_id=case.get("id", f"{request.ticker}:{request.question}"),
        ticker=request.ticker,
        question=request.question,
        expected_evidence_ids=expected_ids,
        actual_evidence_ids=actual_ids,
        min_recall=float(case.get("min_recall", 1.0)),
        missing=missing,
    )


def format_eval_result(
    result: RetrievalGoldEvalResult,
    *,
    max_failures: int = 20,
) -> str:
    lines = [
        f"Retrieval Gold Eval: {result.suite_name}",
        f"file: {result.eval_file}",
        f"cases: {len(result.results)}",
        f"passed: {result.passed_count}",
        f"failed: {result.failed_count}",
        f"pass_rate: {result.pass_rate:.1%}",
    ]

    failed_results = [case for case in result.results if not case.passed]
    if not failed_results:
        return "\n".join(lines)

    lines.append("")
    lines.append("Failures:")
    for case in failed_results[:max_failures]:
        missing_ids = ", ".join(item.evidence_id for item in case.missing) or "none"
        lines.append(f"- {case.case_id}")
        lines.append(f"  query: {case.question}")
        lines.append(f"  recall: {case.recall:.1%} required: {case.min_recall:.1%}")
        lines.append(f"  missing: {missing_ids}")

    remaining = len(failed_results) - max_failures
    if remaining > 0:
        lines.append(f"... {remaining} more failure(s) omitted")
    return "\n".join(lines)


def _json_result(result: RetrievalGoldEvalResult) -> dict[str, Any]:
    return {
        "suite_name": result.suite_name,
        "eval_file": str(result.eval_file),
        "cases": len(result.results),
        "passed": result.passed_count,
        "failed": result.failed_count,
        "pass_rate": result.pass_rate,
        "results": [
            {
                "id": case.case_id,
                "ticker": case.ticker,
                "question": case.question,
                "passed": case.passed,
                "recall": case.recall,
                "min_recall": case.min_recall,
                "missing_evidence_ids": [
                    item.evidence_id for item in case.missing
                ],
                "actual_evidence_ids": case.actual_evidence_ids,
            }
            for case in result.results
        ],
    }


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run retrieval gold-set evals against expected evidence ids.",
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

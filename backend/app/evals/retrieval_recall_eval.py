from __future__ import annotations

import argparse
from dataclasses import dataclass, field
import json
from pathlib import Path
import re
from typing import Any, Iterable, Protocol

from sqlalchemy.orm import Session

from app.db import get_sessionmaker
from app.schemas import RetrievalRequest, RetrievalResponse
from app.services import RetrievalService


DEFAULT_EVAL_FILE = "backend/evals/retrieval_recall_eval.json"
DEFAULT_K = 5
REPORT_KS = (5, 10)


class Retriever(Protocol):
    def retrieve(self, request: RetrievalRequest) -> RetrievalResponse:
        """Return retrieval evidence; retrieved_chunks is the ranked top list."""


def _normalize(text: str) -> str:
    # SEC section labels carry OCR noise ("RI SK FACTORS", "FINANCI AL STATEMENTS"),
    # so compare on alphanumerics only.
    return re.sub(r"[^a-z0-9]", "", (text or "").lower())


@dataclass(frozen=True)
class RetrievalRecallCaseResult:
    case_id: str
    ticker: str
    question: str
    expect_sections: list[str]
    expect_form_type: str | None
    k: int
    hit_rank: int | None  # 1-based rank of the first relevant chunk, or None
    matched_section: str | None = None

    @property
    def passed(self) -> bool:
        return self.hit_rank is not None and self.hit_rank <= self.k

    def hit_within(self, k: int) -> bool:
        return self.hit_rank is not None and self.hit_rank <= k


@dataclass(frozen=True)
class RetrievalRecallEvalResult:
    suite_name: str
    eval_file: Path
    results: list[RetrievalRecallCaseResult]

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

    def recall_at(self, k: int) -> float:
        if not self.results:
            return 0.0
        return sum(1 for result in self.results if result.hit_within(k)) / len(self.results)


def run_eval_file(
    eval_file: str | Path = DEFAULT_EVAL_FILE,
    *,
    db: Session | None = None,
    retriever: Retriever | None = None,
) -> RetrievalRecallEvalResult:
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

    return RetrievalRecallEvalResult(
        suite_name=data.get("suite_name", path.stem),
        eval_file=path,
        results=results,
    )


def evaluate_case(
    case: dict[str, Any],
    retriever: Retriever,
) -> RetrievalRecallCaseResult:
    request = RetrievalRequest(
        ticker=str(case["ticker"]),
        question=str(case["question"]),
        form_type=case.get("form_type"),
        section=case.get("section"),
    )
    k = int(case.get("k", DEFAULT_K))
    expect_sections = [str(section) for section in case.get("expect_sections", [])]
    expect_form_type = case.get("expect_form_type")
    expected_norms = [_normalize(section) for section in expect_sections]

    response = RetrievalResponse.model_validate(retriever.retrieve(request))
    hit_rank: int | None = None
    matched_section: str | None = None
    for rank, chunk in enumerate(response.retrieved_chunks, start=1):
        if expect_form_type and (chunk.form_type or "").upper() != expect_form_type.upper():
            continue
        label_norm = _normalize(chunk.section_label)
        if any(expected and expected in label_norm for expected in expected_norms):
            hit_rank = rank
            matched_section = chunk.section_label
            break

    return RetrievalRecallCaseResult(
        case_id=case.get("id", f"{request.ticker}:{request.question}"),
        ticker=request.ticker,
        question=request.question,
        expect_sections=expect_sections,
        expect_form_type=expect_form_type,
        k=k,
        hit_rank=hit_rank,
        matched_section=matched_section,
    )


def format_eval_result(
    result: RetrievalRecallEvalResult,
    *,
    max_failures: int = 20,
) -> str:
    lines = [
        f"Retrieval Recall Eval: {result.suite_name}",
        f"file: {result.eval_file}",
        f"cases: {len(result.results)}",
        f"recall@5: {result.recall_at(5):.1%}",
        f"recall@10: {result.recall_at(10):.1%}",
    ]

    failed_results = [case for case in result.results if not case.passed]
    if not failed_results:
        return "\n".join(lines)

    lines.append("")
    lines.append("Misses (no relevant section in top-k):")
    for case in failed_results[:max_failures]:
        lines.append(f"- {case.case_id}")
        lines.append(f"  query: {case.question}")
        lines.append(
            f"  expected: {', '.join(case.expect_sections) or 'none'}"
            + (f" [{case.expect_form_type}]" if case.expect_form_type else "")
        )
    remaining = len(failed_results) - max_failures
    if remaining > 0:
        lines.append(f"... {remaining} more miss(es) omitted")
    return "\n".join(lines)


def _json_result(result: RetrievalRecallEvalResult) -> dict[str, Any]:
    return {
        "suite_name": result.suite_name,
        "eval_file": str(result.eval_file),
        "cases": len(result.results),
        "recall_at_5": result.recall_at(5),
        "recall_at_10": result.recall_at(10),
        "results": [
            {
                "id": case.case_id,
                "ticker": case.ticker,
                "question": case.question,
                "passed": case.passed,
                "hit_rank": case.hit_rank,
                "matched_section": case.matched_section,
                "expect_sections": case.expect_sections,
            }
            for case in result.results
        ],
    }


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run text-retrieval recall@k evals (relevant section in top-k).",
    )
    parser.add_argument("eval_file", nargs="?", default=DEFAULT_EVAL_FILE)
    parser.add_argument("--max-failures", type=int, default=20)
    parser.add_argument("--json", action="store_true", dest="json_output")
    parser.add_argument("--no-fail-on-mismatch", action="store_true")
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

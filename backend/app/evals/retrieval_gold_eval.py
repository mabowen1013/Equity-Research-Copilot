from __future__ import annotations

import argparse
from dataclasses import dataclass, field
import json
from pathlib import Path
from typing import Any, Iterable, Protocol

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_sessionmaker
from app.models import Company, Filing
from app.schemas import RetrievalRequest, RetrievalResponse
from app.schemas.retrieval import EvidencePackRead
from app.services import RetrievalService


DEFAULT_EVAL_FILE = "backend/evals/retrieval_gold_eval.json"

# Stable, re-ingest-proof signal: which evidence *role* a question should surface,
# instead of volatile chunk ids seeded from the system's own retrieval dump.
ROLE_PACK_FIELDS: dict[str, tuple[str, ...]] = {
    "metric": ("metric_observations", "metric_comparisons"),
    "primary_financial_statement": (
        "primary_financial_statement_chunks",
        "primary_financial_statement_spans",
    ),
    "mda_explanation": ("mda_explanation_chunks", "mda_explanation_spans"),
    "segment": (
        "segment_or_product_breakdown_chunks",
        "segment_or_product_breakdown_spans",
    ),
    "risk_factor": ("risk_factor_chunks", "risk_factor_spans"),
    "annual_context": ("annual_context_chunks", "annual_context_spans"),
}


class Retriever(Protocol):
    def retrieve(self, request: RetrievalRequest) -> RetrievalResponse:
        """Return retrieval evidence for a request."""


@dataclass(frozen=True)
class RetrievalGoldCaseResult:
    case_id: str
    ticker: str
    question: str
    expected_roles: list[str]
    present_roles: list[str]
    min_role_recall: float
    missing_roles: list[str] = field(default_factory=list)
    form_ok: bool = True
    latest_ok: bool = True
    detail: str | None = None

    @property
    def recall(self) -> float:
        if not self.expected_roles:
            return 1.0
        matched = len(self.expected_roles) - len(self.missing_roles)
        return matched / len(self.expected_roles)

    @property
    def precision(self) -> float:
        # Of the roles we surfaced, how many were expected (focus).
        if not self.present_roles:
            return 0.0
        expected = set(self.expected_roles)
        hit = sum(1 for role in self.present_roles if role in expected)
        return hit / len(self.present_roles)

    @property
    def passed(self) -> bool:
        return self.recall >= self.min_role_recall and self.form_ok and self.latest_ok


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

    @property
    def mean_role_precision(self) -> float:
        if not self.results:
            return 0.0
        return sum(result.precision for result in self.results) / len(self.results)


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
            evaluate_case(case, active_retriever, db=session)
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
    *,
    db: Session | None = None,
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
    present = populated_roles(response)
    expected = [str(role) for role in case.get("expect_roles", [])]
    missing = [role for role in expected if role not in present]

    form_ok = True
    expect_forms = [f.strip().upper() for f in case.get("expect_form_types", [])]
    retrieved_forms = {c.form_type.upper() for c in response.retrieved_chunks if c.form_type}
    if expect_forms:
        form_ok = bool(retrieved_forms & set(expect_forms))

    latest_ok = True
    detail = None
    if case.get("expect_latest") and db is not None:
        latest = latest_filing_date(db, request.ticker, expect_forms or None)
        dates = [c.filing_date.isoformat() for c in response.retrieved_chunks if c.filing_date]
        newest = max(dates) if dates else None
        latest_ok = latest is not None and newest == latest
        if not latest_ok:
            detail = f"newest retrieved filing {newest} != latest {latest}"

    return RetrievalGoldCaseResult(
        case_id=case.get("id", f"{request.ticker}:{request.question}"),
        ticker=request.ticker,
        question=request.question,
        expected_roles=expected,
        present_roles=sorted(present),
        min_role_recall=float(case.get("min_role_recall", 1.0)),
        missing_roles=missing,
        form_ok=form_ok,
        latest_ok=latest_ok,
        detail=detail,
    )


def populated_roles(response: RetrievalResponse) -> set[str]:
    pack: EvidencePackRead = response.final_evidence_pack
    roles: set[str] = set()
    for role, fields in ROLE_PACK_FIELDS.items():
        if any(getattr(pack, field_name) for field_name in fields):
            roles.add(role)
    if response.retrieved_facts:
        roles.add("metric")
    return roles


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
        f"mean_role_precision: {result.mean_role_precision:.1%}",
    ]

    failed_results = [case for case in result.results if not case.passed]
    if not failed_results:
        return "\n".join(lines)

    lines.append("")
    lines.append("Failures:")
    for case in failed_results[:max_failures]:
        missing = ", ".join(case.missing_roles) or "none"
        lines.append(f"- {case.case_id}")
        lines.append(f"  query: {case.question}")
        lines.append(
            f"  role recall: {case.recall:.0%} required: {case.min_role_recall:.0%} "
            f"missing: {missing}"
        )
        lines.append(f"  present roles: {', '.join(case.present_roles) or 'none'}")
        if not case.form_ok:
            lines.append("  form_type expectation not met")
        if case.detail:
            lines.append(f"  {case.detail}")

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
        "mean_role_precision": result.mean_role_precision,
        "results": [
            {
                "id": case.case_id,
                "ticker": case.ticker,
                "question": case.question,
                "passed": case.passed,
                "recall": case.recall,
                "precision": case.precision,
                "min_role_recall": case.min_role_recall,
                "expected_roles": case.expected_roles,
                "present_roles": case.present_roles,
                "missing_roles": case.missing_roles,
                "form_ok": case.form_ok,
                "latest_ok": case.latest_ok,
            }
            for case in result.results
        ],
    }


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run retrieval gold-set evals against expected evidence roles.",
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

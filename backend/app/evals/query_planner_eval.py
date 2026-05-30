from __future__ import annotations

import argparse
from dataclasses import dataclass, field
import json
from pathlib import Path
from typing import Any, Iterable

from app.core.config import Settings
from app.services import QueryPlanner


DEFAULT_EVAL_FILE = "backend/evals/query_planner_ambiguous_slot_eval.json"
SUPPORTED_MATCHERS = {
    "equals",
    "equals_unordered",
    "contains_all",
    "contains_any",
    "not_contains_any",
    "gte",
    "lte",
}


@dataclass(frozen=True)
class FieldMismatch:
    field: str
    matcher: Any
    actual: Any


@dataclass(frozen=True)
class CaseResult:
    case_id: str
    question: str
    mismatches: list[FieldMismatch] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return not self.mismatches


@dataclass(frozen=True)
class EvalResult:
    suite_name: str
    eval_file: Path
    results: list[CaseResult]

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
    eval_file: str | Path,
    *,
    planner: QueryPlanner | None = None,
) -> EvalResult:
    path = Path(eval_file)
    data = json.loads(path.read_text())
    active_planner = planner or QueryPlanner()
    results = [
        evaluate_case(case, active_planner)
        for case in data.get("cases", [])
    ]
    return EvalResult(
        suite_name=data.get("suite_name", path.stem),
        eval_file=path,
        results=results,
    )


def evaluate_case(case: dict[str, Any], planner: QueryPlanner) -> CaseResult:
    question = case["question"]
    actual_plan = planner.plan(question).to_dict()
    mismatches = evaluate_expected_fields(
        actual_plan,
        case.get("expected_plan", {}),
    )
    return CaseResult(
        case_id=case.get("id", question),
        question=question,
        mismatches=mismatches,
    )


def evaluate_expected_fields(
    actual: dict[str, Any],
    expected: dict[str, Any],
) -> list[FieldMismatch]:
    mismatches: list[FieldMismatch] = []
    for field_name, matcher in expected.items():
        actual_value = actual.get(field_name)
        if not match_value(actual_value, matcher):
            mismatches.append(
                FieldMismatch(
                    field=field_name,
                    matcher=matcher,
                    actual=actual_value,
                )
            )
    return mismatches


def match_value(actual: Any, matcher: Any) -> bool:
    if not isinstance(matcher, dict) or len(matcher) != 1:
        return actual == matcher

    operator, expected = next(iter(matcher.items()))
    if operator not in SUPPORTED_MATCHERS:
        raise ValueError(f"Unsupported matcher: {operator}")

    if operator == "equals":
        return actual == expected
    if operator == "equals_unordered":
        return sorted(_as_list(actual)) == sorted(_as_list(expected))
    if operator == "contains_all":
        return set(_as_list(expected)).issubset(set(_as_list(actual)))
    if operator == "contains_any":
        return bool(set(_as_list(expected)).intersection(set(_as_list(actual))))
    if operator == "not_contains_any":
        return not set(_as_list(expected)).intersection(set(_as_list(actual)))
    if operator == "gte":
        return actual >= expected
    if operator == "lte":
        return actual <= expected

    raise ValueError(f"Unsupported matcher: {operator}")


def format_eval_result(
    result: EvalResult,
    *,
    max_failures: int = 20,
) -> str:
    lines = [
        f"Query Planner Eval: {result.suite_name}",
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
        lines.append(f"- {case.case_id}")
        lines.append(f"  query: {case.question}")
        for mismatch in case.mismatches:
            lines.append(
                "  "
                f"{mismatch.field}: expected {json.dumps(mismatch.matcher, ensure_ascii=False)}, "
                f"got {json.dumps(mismatch.actual, ensure_ascii=False)}"
            )

    remaining = len(failed_results) - max_failures
    if remaining > 0:
        lines.append(f"... {remaining} more failure(s) omitted")

    return "\n".join(lines)


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, tuple | set):
        return list(value)
    return [value]


def _json_result(result: EvalResult) -> dict[str, Any]:
    return {
        "suite_name": result.suite_name,
        "eval_file": str(result.eval_file),
        "cases": len(result.results),
        "passed": result.passed_count,
        "failed": result.failed_count,
        "pass_rate": result.pass_rate,
        "failures": [
            {
                "id": case.case_id,
                "question": case.question,
                "mismatches": [
                    {
                        "field": mismatch.field,
                        "expected": mismatch.matcher,
                        "actual": mismatch.actual,
                    }
                    for mismatch in case.mismatches
                ],
            }
            for case in result.results
            if not case.passed
        ],
    }


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run QueryPlanner slot extraction evals.",
    )
    parser.add_argument(
        "eval_file",
        nargs="?",
        default=DEFAULT_EVAL_FILE,
        help=f"Path to eval JSON file. Defaults to {DEFAULT_EVAL_FILE}.",
    )
    parser.add_argument(
        "--planner-mode",
        choices=("llm", "rule_only", "rule_with_llm_fallback"),
        default="llm",
        help="Query planner mode to use during eval. Defaults to llm.",
    )
    parser.add_argument(
        "--llm-model",
        default=None,
        help="Optional LLM model override when planner fallback is enabled.",
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
    settings_kwargs: dict[str, Any] = {
        "query_planner_mode": args.planner_mode,
    }
    if args.llm_model:
        settings_kwargs["query_planner_llm_model"] = args.llm_model

    planner = QueryPlanner(settings=Settings(**settings_kwargs))
    result = run_eval_file(args.eval_file, planner=planner)

    if args.json_output:
        print(json.dumps(_json_result(result), indent=2, ensure_ascii=False))
    else:
        print(format_eval_result(result, max_failures=args.max_failures))

    if result.failed_count and not args.no_fail_on_mismatch:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

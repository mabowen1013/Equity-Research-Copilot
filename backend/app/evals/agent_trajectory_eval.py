from __future__ import annotations

import argparse
from dataclasses import dataclass, field
import json
from pathlib import Path
from typing import Any, Iterable, Protocol

from sqlalchemy.orm import Session

from app.db import get_sessionmaker
from app.schemas import RetrievalRequest, RetrievalResponse
from app.services import RetrievalService
from app.services.research_agent import REACT_TOOL_ACTIONS

# The LLM controller is non-deterministic, so tool selection is a real regression
# surface. Each case runs N times and gates on every run choosing the expected
# tools; stability is the share of runs that passed.
try:  # clearing the shared LLM cache makes repeated runs real calls, not cache hits
    from app.services.query_planner import clear_llm_response_cache
except Exception:  # pragma: no cover - cache is optional
    def clear_llm_response_cache() -> None:  # type: ignore
        return None


DEFAULT_EVAL_FILE = "backend/evals/agent_trajectory_eval.json"
DEFAULT_RUNS = 3


class Retriever(Protocol):
    def retrieve(self, request: RetrievalRequest) -> RetrievalResponse:
        """Return retrieval evidence (whose trace records the agent's tool steps)."""


@dataclass(frozen=True)
class TrajectoryRun:
    used_tools: list[str]
    missing: list[str]
    forbidden_used: list[str]

    @property
    def passed(self) -> bool:
        return not self.missing and not self.forbidden_used


@dataclass(frozen=True)
class AgentTrajectoryCaseResult:
    case_id: str
    ticker: str
    question: str
    expect_tools: list[str]
    forbid_tools: list[str]
    runs: list[TrajectoryRun] = field(default_factory=list)

    @property
    def stability(self) -> float:
        if not self.runs:
            return 0.0
        return sum(1 for run in self.runs if run.passed) / len(self.runs)

    @property
    def passed(self) -> bool:
        return bool(self.runs) and all(run.passed for run in self.runs)


@dataclass(frozen=True)
class AgentTrajectoryEvalResult:
    suite_name: str
    eval_file: Path
    results: list[AgentTrajectoryCaseResult]

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
    def mean_stability(self) -> float:
        if not self.results:
            return 0.0
        return sum(result.stability for result in self.results) / len(self.results)


def run_eval_file(
    eval_file: str | Path = DEFAULT_EVAL_FILE,
    *,
    db: Session | None = None,
    retriever: Retriever | None = None,
) -> AgentTrajectoryEvalResult:
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

    return AgentTrajectoryEvalResult(
        suite_name=data.get("suite_name", path.stem),
        eval_file=path,
        results=results,
    )


def evaluate_case(
    case: dict[str, Any],
    retriever: Retriever,
) -> AgentTrajectoryCaseResult:
    request = RetrievalRequest(
        ticker=str(case["ticker"]),
        question=str(case["question"]),
        form_type=case.get("form_type"),
        section=case.get("section"),
    )
    expect_tools = [str(tool) for tool in case.get("expect_tools", [])]
    forbid_tools = [str(tool) for tool in case.get("forbid_tools", [])]
    runs_n = int(case.get("runs", DEFAULT_RUNS))

    runs: list[TrajectoryRun] = []
    for _ in range(runs_n):
        clear_llm_response_cache()
        response = RetrievalResponse.model_validate(retriever.retrieve(request))
        used = used_tools(response)
        runs.append(
            TrajectoryRun(
                used_tools=sorted(used),
                missing=[tool for tool in expect_tools if tool not in used],
                forbidden_used=[tool for tool in forbid_tools if tool in used],
            )
        )

    return AgentTrajectoryCaseResult(
        case_id=case.get("id", f"{request.ticker}:{request.question}"),
        ticker=request.ticker,
        question=request.question,
        expect_tools=expect_tools,
        forbid_tools=forbid_tools,
        runs=runs,
    )


def used_tools(response: RetrievalResponse) -> set[str]:
    agent = response.retrieval_trace.get("agent", {}) if response.retrieval_trace else {}
    return {
        step.get("action")
        for step in agent.get("steps", [])
        if step.get("action") in REACT_TOOL_ACTIONS
    }


def format_eval_result(
    result: AgentTrajectoryEvalResult,
    *,
    max_failures: int = 20,
) -> str:
    lines = [
        f"Agent Trajectory Eval: {result.suite_name}",
        f"file: {result.eval_file}",
        f"cases: {len(result.results)}",
        f"passed: {result.passed_count}",
        f"failed: {result.failed_count}",
        f"pass_rate: {result.pass_rate:.1%}",
        f"mean_stability: {result.mean_stability:.1%}",
    ]

    failed_results = [case for case in result.results if not case.passed]
    if not failed_results:
        return "\n".join(lines)

    lines.append("")
    lines.append("Failures:")
    for case in failed_results[:max_failures]:
        lines.append(f"- {case.case_id}  (stability {case.stability:.0%})")
        lines.append(f"  query: {case.question}")
        lines.append(f"  expect: {', '.join(case.expect_tools) or 'none'}")
        for index, run in enumerate(case.runs):
            if not run.passed:
                lines.append(
                    f"  run {index}: used [{', '.join(run.used_tools) or 'none'}] "
                    f"missing [{', '.join(run.missing) or 'none'}] "
                    f"forbidden [{', '.join(run.forbidden_used) or 'none'}]"
                )

    remaining = len(failed_results) - max_failures
    if remaining > 0:
        lines.append(f"... {remaining} more failure(s) omitted")
    return "\n".join(lines)


def _json_result(result: AgentTrajectoryEvalResult) -> dict[str, Any]:
    return {
        "suite_name": result.suite_name,
        "eval_file": str(result.eval_file),
        "cases": len(result.results),
        "passed": result.passed_count,
        "failed": result.failed_count,
        "pass_rate": result.pass_rate,
        "mean_stability": result.mean_stability,
        "results": [
            {
                "id": case.case_id,
                "ticker": case.ticker,
                "question": case.question,
                "passed": case.passed,
                "stability": case.stability,
                "expect_tools": case.expect_tools,
                "forbid_tools": case.forbid_tools,
                "runs": [
                    {
                        "used_tools": run.used_tools,
                        "missing": run.missing,
                        "forbidden_used": run.forbidden_used,
                    }
                    for run in case.runs
                ],
            }
            for case in result.results
        ],
    }


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run agent tool-trajectory evals against the LLM ReAct controller.",
    )
    parser.add_argument(
        "eval_file",
        nargs="?",
        default=DEFAULT_EVAL_FILE,
        help=f"Path to eval JSON file. Defaults to {DEFAULT_EVAL_FILE}.",
    )
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

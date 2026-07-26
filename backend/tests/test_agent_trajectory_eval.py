import json

from app.evals.agent_trajectory_eval import (
    evaluate_case,
    format_eval_result,
    run_eval_file,
)
from app.schemas import RetrievalRequest

from .test_answer_context import make_response


def make_trace_response(actions: list[str]):
    """A retrieval response whose agent trace records the given tool actions."""
    response = make_response()
    response.retrieval_trace = {
        "agent": {
            "mode": "react_llm",
            "steps": [
                {"step": 0, "action": "analyze_question"},
                *[{"step": i + 1, "action": a} for i, a in enumerate(actions)],
                {"step": len(actions) + 1, "action": "finalize_answer"},
            ],
        }
    }
    return response


class FakeRetriever:
    def __init__(self, actions: list[str]) -> None:
        self._actions = actions
        self.calls = 0

    def retrieve(self, request: RetrievalRequest):
        self.calls += 1
        return make_trace_response(self._actions)


def test_trajectory_passes_when_expected_tools_used() -> None:
    result = evaluate_case(
        {
            "id": "metric",
            "ticker": "AAPL",
            "question": "What was revenue?",
            "expect_tools": ["query_xbrl_metrics"],
            "runs": 2,
        },
        FakeRetriever(["query_xbrl_metrics", "retrieve_filing_chunks"]),
    )

    assert result.passed
    assert result.stability == 1.0


def test_trajectory_fails_when_expected_tool_missing() -> None:
    result = evaluate_case(
        {
            "id": "why",
            "ticker": "AAPL",
            "question": "Why did margin change?",
            "expect_tools": ["query_xbrl_metrics", "retrieve_mda"],
            "runs": 2,
        },
        FakeRetriever(["query_xbrl_metrics"]),
    )

    assert not result.passed
    assert result.runs[0].missing == ["retrieve_mda"]


def test_trajectory_fails_on_forbidden_tool() -> None:
    result = evaluate_case(
        {
            "id": "risk",
            "ticker": "MSFT",
            "question": "Risks?",
            "expect_tools": ["retrieve_risk_factors"],
            "forbid_tools": ["query_xbrl_metrics"],
            "runs": 1,
        },
        FakeRetriever(["retrieve_risk_factors", "query_xbrl_metrics"]),
    )

    assert not result.passed
    assert result.runs[0].forbidden_used == ["query_xbrl_metrics"]


def test_run_eval_file_reports_pass_and_fail(tmp_path) -> None:
    eval_file = tmp_path / "agent_trajectory.json"
    eval_file.write_text(
        json.dumps(
            {
                "suite_name": "sample_trajectory",
                "cases": [
                    {
                        "id": "ok",
                        "ticker": "AAPL",
                        "question": "What was revenue?",
                        "expect_tools": ["query_xbrl_metrics"],
                        "runs": 1,
                    }
                ],
            }
        )
    )

    result = run_eval_file(eval_file, retriever=FakeRetriever(["query_xbrl_metrics"]))

    assert result.pass_rate == 1.0
    assert result.mean_stability == 1.0
    assert "Agent Trajectory Eval" in format_eval_result(result)

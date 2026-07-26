import json

from app.evals.retrieval_gold_eval import (
    evaluate_case,
    format_eval_result,
    run_eval_file,
)
from app.schemas import RetrievalRequest

from .test_answer_context import make_response


def test_evaluate_case_passes_when_expected_roles_present() -> None:
    result = evaluate_case(
        {
            "id": "sample_case",
            "ticker": "AAPL",
            "question": "What was latest revenue?",
            "expect_roles": ["primary_financial_statement", "metric"],
            "min_role_recall": 1.0,
            "expect_form_types": ["10-Q"],
        },
        FakeRetriever(),
    )

    assert result.passed
    assert result.recall == 1.0
    assert result.missing_roles == []
    assert result.form_ok


def test_evaluate_case_reports_missing_roles() -> None:
    result = evaluate_case(
        {
            "id": "missing_case",
            "ticker": "AAPL",
            "question": "What are the risks?",
            "expect_roles": ["risk_factor"],
            "min_role_recall": 1.0,
        },
        FakeRetriever(),
    )

    assert not result.passed
    assert result.recall == 0.0
    assert result.missing_roles == ["risk_factor"]


def test_evaluate_case_fails_on_wrong_form_type() -> None:
    result = evaluate_case(
        {
            "id": "form_case",
            "ticker": "AAPL",
            "question": "What was latest revenue?",
            "expect_roles": ["primary_financial_statement"],
            "min_role_recall": 1.0,
            "expect_form_types": ["10-K"],
        },
        FakeRetriever(),
    )

    assert not result.form_ok
    assert not result.passed


def test_run_eval_file_with_fake_retriever(tmp_path) -> None:
    eval_file = tmp_path / "retrieval_gold.json"
    eval_file.write_text(
        json.dumps(
            {
                "suite_name": "sample_retrieval_gold",
                "cases": [
                    {
                        "id": "sample_pass",
                        "ticker": "AAPL",
                        "question": "What was latest revenue?",
                        "expect_roles": ["primary_financial_statement"],
                        "min_role_recall": 1.0,
                    },
                    {
                        "id": "sample_fail",
                        "ticker": "AAPL",
                        "question": "What are the risks?",
                        "expect_roles": ["risk_factor"],
                        "min_role_recall": 1.0,
                    },
                ],
            }
        )
    )

    result = run_eval_file(eval_file, retriever=FakeRetriever())

    assert result.suite_name == "sample_retrieval_gold"
    assert result.passed_count == 1
    assert result.failed_count == 1
    summary = format_eval_result(result)
    assert "sample_fail" in summary
    assert "risk_factor" in summary


class FakeRetriever:
    def retrieve(self, request: RetrievalRequest):
        return make_response()

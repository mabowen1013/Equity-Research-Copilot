import json

from app.evals.retrieval_gold_eval import (
    evaluate_case,
    format_eval_result,
    run_eval_file,
)
from app.schemas import RetrievalRequest

from .test_answer_context import make_response


def test_evaluate_case_passes_when_expected_evidence_ids_are_present() -> None:
    result = evaluate_case(
        {
            "id": "sample_case",
            "ticker": "AAPL",
            "question": "What was latest revenue?",
            "expected_evidence_ids": [
                "chunk:101",
                "span:101:primary_financial_statement_chunks:0:80",
            ],
        },
        FakeRetriever(),
    )

    assert result.passed
    assert result.recall == 1.0
    assert result.missing == []


def test_evaluate_case_reports_missing_evidence_ids() -> None:
    result = evaluate_case(
        {
            "id": "missing_case",
            "ticker": "AAPL",
            "question": "What was latest revenue?",
            "expected_evidence_ids": ["chunk:999"],
        },
        FakeRetriever(),
    )

    assert not result.passed
    assert result.recall == 0.0
    assert result.missing[0].evidence_id == "chunk:999"


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
                        "expected_evidence_ids": ["chunk:101"],
                    },
                    {
                        "id": "sample_fail",
                        "ticker": "AAPL",
                        "question": "What was latest revenue?",
                        "expected_evidence_ids": ["chunk:999"],
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
    assert "missing: chunk:999" in summary


class FakeRetriever:
    def retrieve(self, request: RetrievalRequest):
        return make_response()

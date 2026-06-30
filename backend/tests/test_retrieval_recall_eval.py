import json

from app.evals.retrieval_recall_eval import (
    evaluate_case,
    format_eval_result,
    run_eval_file,
)
from app.schemas import RetrievalRequest

from .test_answer_context import make_response


class FakeRetriever:
    """Returns a response whose retrieved_chunks have the given section labels."""

    def __init__(self, section_labels: list[str]) -> None:
        self._labels = section_labels

    def retrieve(self, request: RetrievalRequest):
        response = make_response()
        template = response.retrieved_chunks[0]
        response.retrieved_chunks = [
            template.model_copy(update={"chunk_id": index, "section_label": label})
            for index, label in enumerate(self._labels, start=1)
        ]
        return response


def test_recall_hit_when_expected_section_in_top_k() -> None:
    result = evaluate_case(
        {"id": "risk", "ticker": "AAPL", "question": "risks?",
         "expect_sections": ["RISK FACTORS"], "k": 5},
        FakeRetriever(["PART I - ITEM 2 - MD&A", "PART II - ITEM 1A - RISK FACTORS"]),
    )
    assert result.passed
    assert result.hit_rank == 2


def test_recall_normalizes_ocr_noise_in_labels() -> None:
    # "RI SK FACTORS" must still match the expected "RISK FACTORS".
    result = evaluate_case(
        {"id": "risk", "ticker": "AAPL", "question": "risks?",
         "expect_sections": ["RISK FACTORS"], "k": 5},
        FakeRetriever(["PART II - ITEM 1A - RI SK FACTORS"]),
    )
    assert result.passed
    assert result.hit_rank == 1


def test_recall_miss_when_section_absent_or_beyond_k() -> None:
    miss = evaluate_case(
        {"id": "risk", "ticker": "AAPL", "question": "risks?",
         "expect_sections": ["RISK FACTORS"], "k": 5},
        FakeRetriever(["MD&A", "FINANCIAL STATEMENTS", "BUSINESS"]),
    )
    assert not miss.passed
    assert miss.hit_rank is None

    beyond = evaluate_case(
        {"id": "risk", "ticker": "AAPL", "question": "risks?",
         "expect_sections": ["RISK FACTORS"], "k": 1},
        FakeRetriever(["MD&A", "RISK FACTORS"]),
    )
    assert not beyond.passed  # hit at rank 2 but k=1
    assert beyond.hit_rank == 2


def test_run_eval_file_reports_recall(tmp_path) -> None:
    eval_file = tmp_path / "recall.json"
    eval_file.write_text(
        json.dumps(
            {
                "suite_name": "sample_recall",
                "cases": [
                    {"id": "hit", "ticker": "AAPL", "question": "risks?",
                     "expect_sections": ["RISK FACTORS"], "k": 5}
                ],
            }
        )
    )
    result = run_eval_file(
        eval_file, retriever=FakeRetriever(["RISK FACTORS"])
    )
    assert result.recall_at(5) == 1.0
    assert "recall@5" in format_eval_result(result)

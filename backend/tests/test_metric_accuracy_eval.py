import json
from dataclasses import dataclass

from app.evals.metric_accuracy_eval import (
    evaluate_case,
    primary_amount_matches,
    run_eval_file,
    wilson_interval,
)
from decimal import Decimal


@dataclass
class FakeAnswer:
    answer: str
    validation_status: str = "passed"


class FakeAnswerService:
    def __init__(self, answer: str, status: str = "passed") -> None:
        self._answer = answer
        self._status = status

    def answer(self, request):
        return FakeAnswer(self._answer, self._status)


def test_primary_amount_matches_uses_first_dollar_figure() -> None:
    # First figure is the headline; a correct prior-year figure later must NOT rescue it.
    wrong = "Revenue was $416.16 billion for FY2024, up from $391.04 billion the prior year."
    assert not primary_amount_matches(wrong, Decimal("391035000000"), Decimal("0.03"))

    right = "Revenue was $391.0 billion in FY2024, up from $383.3 billion."
    assert primary_amount_matches(right, Decimal("391035000000"), Decimal("0.03"))

    assert not primary_amount_matches("No figures here.", Decimal("391035000000"), Decimal("0.03"))


def test_evaluate_case_classifies_correct_wrong_declined() -> None:
    case = {"id": "c", "ticker": "AAPL", "question": "rev?", "kind": "amount",
            "value": 391035000000.0, "rel_tol": 0.03, "metric": "revenue", "fiscal_year": 2024}

    correct = evaluate_case(case, FakeAnswerService("Revenue was $391.0B in FY2024. [chunk:1]"))
    assert correct.status == "correct"

    wrong = evaluate_case(case, FakeAnswerService("Revenue was $416.2B in FY2024. [chunk:1]"))
    assert wrong.status == "wrong"

    declined = evaluate_case(case, FakeAnswerService("insufficient", status="insufficient_evidence"))
    assert declined.status == "declined"


def test_wilson_interval_bounds() -> None:
    lo, hi = wilson_interval(8, 10)
    assert 0.0 <= lo < 0.8 < hi <= 1.0
    assert wilson_interval(0, 0) == (0.0, 0.0)


def test_run_eval_file_aggregates(tmp_path) -> None:
    eval_file = tmp_path / "ma.json"
    eval_file.write_text(json.dumps({"suite_name": "s", "cases": [
        {"id": "a", "ticker": "AAPL", "question": "rev?", "kind": "amount", "value": 100.0, "rel_tol": 0.03},
        {"id": "b", "ticker": "AAPL", "question": "rev?", "kind": "amount", "value": 999.0, "rel_tol": 0.03},
    ]}))
    # answer states $100 -> case a correct, case b wrong
    result = run_eval_file(eval_file, answer_service=FakeAnswerService("It was $100. [chunk:1]"))
    assert result.total == 2
    assert result.correct == 1
    assert 0.0 < result.accuracy < 1.0

import json

from app.evals.query_planner_eval import (
    evaluate_expected_fields,
    format_eval_result,
    match_value,
    run_eval_file,
)


def test_match_value_supports_list_matchers() -> None:
    assert match_value(["revenue", "net_income"], {"equals_unordered": ["net_income", "revenue"]})
    assert match_value(["10-Q", "10-K"], {"contains_all": ["10-Q"]})
    assert match_value(["10-Q"], {"contains_any": ["10-Q", "10-K"]})
    assert match_value(["revenue"], {"not_contains_any": ["stock_price"]})

    assert not match_value(["revenue"], {"equals_unordered": ["revenue", "net_income"]})
    assert not match_value(["10-Q"], {"contains_all": ["10-Q", "10-K"]})


def test_evaluate_expected_fields_reports_mismatches() -> None:
    mismatches = evaluate_expected_fields(
        {
            "question_type": "metric",
            "metric_keys": ["revenue"],
        },
        {
            "question_type": {"equals": "trend"},
            "metric_keys": {"contains_all": ["revenue"]},
        },
    )

    assert len(mismatches) == 1
    assert mismatches[0].field == "question_type"
    assert mismatches[0].actual == "metric"


def test_run_eval_file_with_fake_planner(tmp_path) -> None:
    eval_file = tmp_path / "query_eval.json"
    eval_file.write_text(
        json.dumps(
            {
                "suite_name": "sample_query_planner_eval",
                "cases": [
                    {
                        "id": "sample_growth",
                        "question": "Is Apple growing?",
                        "expected_plan": {
                            "question_type": {"equals": "trend"},
                            "metric_keys": {"contains_all": ["revenue"]},
                        },
                    }
                ],
            }
        )
    )

    result = run_eval_file(eval_file, planner=FakePlanner())

    assert result.suite_name == "sample_query_planner_eval"
    assert result.passed_count == 1
    assert result.failed_count == 0
    assert "passed: 1" in format_eval_result(result)


class FakePlanner:
    def plan(self, question: str) -> "FakePlan":
        return FakePlan(
            {
                "question_type": "trend",
                "metric_keys": ["revenue", "operating_income", "net_income"],
            }
        )


class FakePlan:
    def __init__(self, payload: dict) -> None:
        self.payload = payload

    def to_dict(self) -> dict:
        return self.payload

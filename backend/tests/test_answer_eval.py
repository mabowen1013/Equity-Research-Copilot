from __future__ import annotations

import json

from app.evals.answer_eval import (
    AnswerGoldEvalResult,
    check_case,
    format_eval_result,
    run_eval_file,
)
from app.schemas import RetrievalRequest
from app.schemas.answer import (
    AnswerCitationRead,
    CitationValidationIssueRead,
    CitationValidationRead,
)
from app.schemas.research_run import ResearchRunRead


def build_run(
    *,
    answer: str,
    validation_status: str = "passed",
    citations: list[AnswerCitationRead] | None = None,
    claim_sentence_count: int = 2,
    cited_claim_sentence_count: int = 2,
    duration_ms: float = 1200.0,
    warnings: list[CitationValidationIssueRead] | None = None,
    errors: list[CitationValidationIssueRead] | None = None,
) -> ResearchRunRead:
    cited_ids = [citation.evidence_id for citation in (citations or [])]
    return ResearchRunRead(
        run_id="run_test",
        status="completed" if validation_status == "passed" else validation_status,
        ticker="AAPL",
        question="What was Apple's revenue in the latest quarter?",
        duration_ms=duration_ms,
        answer=answer,
        citations=citations or [],
        validation_status=validation_status,
        validation=CitationValidationRead(
            status="passed" if validation_status == "passed" else "failed",
            cited_evidence_ids=cited_ids,
            allowed_evidence_ids=cited_ids,
            prompt_evidence_ids=cited_ids,
            claim_sentence_count=claim_sentence_count,
            cited_claim_sentence_count=cited_claim_sentence_count,
            warnings=warnings or [],
            errors=errors or [],
        ),
        plan={},
    )


class FakeRunner:
    def __init__(self, run: ResearchRunRead) -> None:
        self._run = run
        self.requests: list[RetrievalRequest] = []

    def run(self, request: RetrievalRequest) -> ResearchRunRead:
        self.requests.append(request)
        return self._run


def write_eval_file(tmp_path, cases: list[dict]) -> str:
    path = tmp_path / "answer_eval.json"
    path.write_text(
        json.dumps({"suite_name": "answer_eval_test", "cases": cases})
    )
    return str(path)


def test_answer_eval_passes_for_cited_answer(tmp_path) -> None:
    citation = AnswerCitationRead(evidence_id="chunk:1", evidence_type="chunk")
    runner = FakeRunner(
        build_run(
            answer="Revenue was $94.0B in the latest quarter. [chunk:1]",
            citations=[citation],
        )
    )
    eval_file = write_eval_file(
        tmp_path,
        [
            {
                "id": "case_pass",
                "ticker": "AAPL",
                "question": "What was Apple's revenue in the latest quarter?",
                "min_citations": 1,
                "must_match": ["\\$\\d"],
                "min_claim_citation_coverage": 0.5,
                "max_duration_ms": 30000,
            }
        ],
    )

    result = run_eval_file(eval_file, runner=runner)

    assert isinstance(result, AnswerGoldEvalResult)
    assert result.pass_rate == 1.0
    assert result.results[0].claim_citation_coverage == 1.0
    assert runner.requests[0].ticker == "AAPL"


def test_answer_eval_fails_on_forbidden_advice_pattern(tmp_path) -> None:
    citation = AnswerCitationRead(evidence_id="chunk:1", evidence_type="chunk")
    runner = FakeRunner(
        build_run(
            answer="Revenue grew, so we recommend buying the stock. [chunk:1]",
            citations=[citation],
        )
    )
    eval_file = write_eval_file(
        tmp_path,
        [
            {
                "id": "case_advice",
                "ticker": "AAPL",
                "question": "How did Apple perform?",
            }
        ],
    )

    result = run_eval_file(eval_file, runner=runner)

    assert result.failed_count == 1
    failure_codes = [failure.code for failure in result.results[0].failures]
    assert "must_not_match" in failure_codes


def test_answer_eval_fails_on_low_claim_citation_coverage(tmp_path) -> None:
    citation = AnswerCitationRead(evidence_id="chunk:1", evidence_type="chunk")
    runner = FakeRunner(
        build_run(
            answer="Revenue was $94.0B. [chunk:1] Margins also improved a lot.",
            citations=[citation],
            claim_sentence_count=4,
            cited_claim_sentence_count=1,
        )
    )
    eval_file = write_eval_file(
        tmp_path,
        [
            {
                "id": "case_coverage",
                "ticker": "AAPL",
                "question": "How did Apple perform?",
                "min_claim_citation_coverage": 0.8,
            }
        ],
    )

    result = run_eval_file(eval_file, runner=runner)

    assert result.failed_count == 1
    failure_codes = [failure.code for failure in result.results[0].failures]
    assert "claim_citation_coverage" in failure_codes


def test_answer_eval_checks_validation_status_and_latency(tmp_path) -> None:
    runner = FakeRunner(
        build_run(
            answer="I do not have enough validated evidence to answer.",
            validation_status="insufficient_evidence",
            claim_sentence_count=0,
            cited_claim_sentence_count=0,
            duration_ms=50000.0,
        )
    )
    eval_file = write_eval_file(
        tmp_path,
        [
            {
                "id": "case_status",
                "ticker": "AAPL",
                "question": "What is the CEO's favorite color?",
                "expect_validation_status": "insufficient_evidence",
                "min_citations": 0,
                "max_duration_ms": 30000,
            }
        ],
    )

    result = run_eval_file(eval_file, runner=runner)

    assert result.failed_count == 1
    failure_codes = [failure.code for failure in result.results[0].failures]
    assert failure_codes == ["max_duration_ms"]


def test_answer_eval_value_match_enforces_numeric_truth(tmp_path) -> None:
    citation = AnswerCitationRead(evidence_id="chunk:1", evidence_type="chunk")
    runner = FakeRunner(
        build_run(
            answer="Apple's total revenue was $416.2B for fiscal 2025. [chunk:1]",
            citations=[citation],
        )
    )
    eval_file = write_eval_file(
        tmp_path,
        [
            {
                "id": "value_ok",
                "ticker": "AAPL",
                "question": "Apple FY revenue?",
                "expect_values": [{"kind": "amount", "value": 416161000000, "rel_tol": 0.02}],
            },
            {
                "id": "value_bad",
                "ticker": "AAPL",
                "question": "Apple FY revenue?",
                "expect_values": [{"kind": "amount", "value": 950000000000}],
            },
        ],
    )

    result = run_eval_file(eval_file, runner=runner)

    by_id = {case.case_id: case for case in result.results}
    assert by_id["value_ok"].passed
    assert "value_mismatch" in [f.code for f in by_id["value_bad"].failures]


def test_answer_eval_promotes_grounding_warning_to_failure(tmp_path) -> None:
    citation = AnswerCitationRead(evidence_id="chunk:1", evidence_type="chunk")
    runner = FakeRunner(
        build_run(
            answer="Revenue was $950B. [chunk:1]",
            citations=[citation],
            warnings=[
                CitationValidationIssueRead(
                    code="unsupported_number", message="not supported"
                )
            ],
        )
    )
    eval_file = write_eval_file(
        tmp_path,
        [
            {
                "id": "warn",
                "ticker": "AAPL",
                "question": "Revenue?",
                "max_warning_codes": {"unsupported_number": 0},
            }
        ],
    )

    result = run_eval_file(eval_file, runner=runner)

    assert result.failed_count == 1
    assert "warning_cap" in [f.code for f in result.results[0].failures]
    assert result.results[0].grounding_warnings == 1


def test_answer_eval_contradicted_claim_gate(tmp_path) -> None:
    citation = AnswerCitationRead(evidence_id="chunk:1", evidence_type="chunk")
    runner = FakeRunner(
        build_run(
            answer="Apple will discontinue the iPhone. [chunk:1]",
            citations=[citation],
            errors=[
                CitationValidationIssueRead(
                    code="contradicted_claim", message="evidence contradicts"
                )
            ],
        )
    )
    eval_file = write_eval_file(
        tmp_path,
        [{"id": "contra", "ticker": "AAPL", "question": "iPhone plans?"}],
    )

    result = run_eval_file(eval_file, runner=runner)

    assert "contradicted_claim" in [f.code for f in result.results[0].failures]
    assert result.results[0].contradicted_claims == 1


def test_check_case_flags_stale_filing() -> None:
    run = build_run(
        answer="Revenue was $94.0B. [chunk:1]",
        citations=[
            AnswerCitationRead(
                evidence_id="chunk:1", evidence_type="chunk", filing_date="2024-08-01"
            )
        ],
    )
    failures = check_case(
        {"id": "p", "ticker": "AAPL", "question": "q", "expect_latest_filing": True},
        run,
        latest_filing_date="2026-05-01",
    )

    assert "stale_filing" in [f.code for f in failures]


def test_format_eval_result_lists_failures(tmp_path) -> None:
    runner = FakeRunner(build_run(answer="", validation_status="failed"))
    eval_file = write_eval_file(
        tmp_path,
        [
            {
                "id": "case_format",
                "ticker": "AAPL",
                "question": "How did Apple perform?",
            }
        ],
    )

    result = run_eval_file(eval_file, runner=runner)
    report = format_eval_result(result)

    assert "Answer Gold Eval" in report
    assert "case_format" in report
    assert "validation_status" in report

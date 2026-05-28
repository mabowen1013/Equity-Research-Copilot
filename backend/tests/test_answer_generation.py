from app.schemas import (
    EvidencePackRead,
    GeneratedAnswerCandidate,
    ResearchQueryRequest,
    RetrievalResponse,
)
from app.services import (
    AnswerService,
    build_answer_evidence_context,
    build_prompt_evidence_context,
    extract_citation_ids,
    validate_generated_answer,
)

from .test_answer_context import make_response


def test_extract_citation_ids_supports_nested_colon_ids() -> None:
    answer = (
        "Revenue changed [metric_comparison:revenue:latest_quarter_yoy:20:21] "
        "and risk factors matter [span:130:risk_factor_chunks:0:120]."
    )

    assert extract_citation_ids(answer) == [
        "metric_comparison:revenue:latest_quarter_yoy:20:21",
        "span:130:risk_factor_chunks:0:120",
    ]


def test_valid_generated_answer_with_prompt_citations_passes() -> None:
    validation = validate_answer(
        "Revenue was $111.2 billion [financial_fact:501]. "
        "Overall, the evidence points to stronger recent performance."
    )

    assert validation.status == "passed"
    assert validation.errors == []


def test_unknown_citation_id_fails_validation() -> None:
    validation = validate_answer("Revenue was $111.2 billion [financial_fact:999].")

    assert validation.status == "failed"
    assert {issue.code for issue in validation.errors} == {"unknown_citation"}


def test_allowed_but_not_prompt_visible_citation_fails_validation() -> None:
    validation = validate_answer("Revenue was $111.2 billion [chunk:101].")

    assert validation.status == "failed"
    assert "out_of_prompt_citation" in {issue.code for issue in validation.errors}


def test_uncited_factual_sentence_fails_validation() -> None:
    validation = validate_answer("Revenue was $111.2 billion.")

    assert validation.status == "failed"
    assert "missing_citations" in {issue.code for issue in validation.errors}
    assert "uncited_factual_claim" in {issue.code for issue in validation.errors}


def test_uncited_limitation_sentence_passes_with_other_valid_citation() -> None:
    validation = validate_answer(
        "Revenue was $111.2 billion [financial_fact:501]. "
        "However, the available evidence is limited to SEC filings."
    )

    assert validation.status == "passed"


def test_financial_number_requires_fact_or_comparison_citation_when_available() -> None:
    validation = validate_answer(
        "Revenue was $111.2 billion [span:101:primary_financial_statement_chunks:0:80]."
    )

    assert validation.status == "failed"
    assert "missing_financial_fact_citation" in {issue.code for issue in validation.errors}


def test_investment_advice_language_fails_validation() -> None:
    validation = validate_answer(
        "Revenue was $111.2 billion [financial_fact:501]. Investors should buy the stock."
    )

    assert validation.status == "failed"
    assert "investment_advice" in {issue.code for issue in validation.errors}


def test_answer_service_returns_insufficient_evidence_without_llm_call() -> None:
    provider = FakeProvider([])
    service = AnswerService(
        FakeSession(),
        retriever=FakeRetriever(make_empty_response()),
        provider=provider,
    )

    response = service.answer(make_request())

    assert response.validation_status == "insufficient_evidence"
    assert response.citations == []
    assert provider.calls == 0
    assert response.validation.errors[0].code == "no_prompt_evidence"


def test_answer_service_retries_once_after_validation_failure() -> None:
    provider = FakeProvider(
        [
            GeneratedAnswerCandidate(answer="Revenue was $111.2 billion."),
            GeneratedAnswerCandidate(answer="Revenue was $111.2 billion [financial_fact:501]."),
        ]
    )
    service = AnswerService(
        FakeSession(),
        retriever=FakeRetriever(make_response()),
        provider=provider,
    )

    response = service.answer(make_request())

    assert response.validation_status == "passed"
    assert provider.calls == 2
    assert provider.prompts[1].count("Previous answer failed validation") == 1
    assert response.citations[0].evidence_id == "financial_fact:501"


def test_answer_service_repairs_missing_financial_citation_when_text_citation_is_valid() -> None:
    provider = FakeProvider(
        [
            GeneratedAnswerCandidate(
                answer=(
                    "Revenue was $111.2 billion "
                    "[span:101:primary_financial_statement_chunks:0:80]."
                )
            ),
        ]
    )
    service = AnswerService(
        FakeSession(),
        retriever=FakeRetriever(make_response()),
        provider=provider,
    )

    response = service.answer(make_request())

    assert response.validation_status == "passed"
    assert provider.calls == 1
    assert (
        "Revenue was $111.2 billion "
        "[span:101:primary_financial_statement_chunks:0:80] [financial_fact:501]."
    ) == response.answer
    assert [citation.evidence_id for citation in response.citations] == [
        "span:101:primary_financial_statement_chunks:0:80",
        "financial_fact:501",
    ]


def test_answer_service_returns_insufficient_evidence_after_two_invalid_generations() -> None:
    provider = FakeProvider(
        [
            GeneratedAnswerCandidate(answer="Revenue was $111.2 billion."),
            GeneratedAnswerCandidate(answer="Revenue was $111.2 billion [chunk:101]."),
        ]
    )
    service = AnswerService(
        FakeSession(),
        retriever=FakeRetriever(make_response()),
        provider=provider,
    )

    response = service.answer(make_request())

    assert response.validation_status == "insufficient_evidence"
    assert provider.calls == 2
    assert response.citations == []
    assert response.validation.status == "failed"


def validate_answer(answer: str):
    request = make_request()
    context = build_answer_evidence_context(request, make_response())
    prompt_context = build_prompt_evidence_context(context)
    return validate_generated_answer(
        GeneratedAnswerCandidate(answer=answer),
        context,
        prompt_context,
    )


def make_request() -> ResearchQueryRequest:
    return ResearchQueryRequest(ticker="AAPL", question="What was latest revenue?")


def make_empty_response() -> RetrievalResponse:
    response = make_response()
    response.retrieved_chunks = []
    response.retrieved_facts = []
    response.metric_comparisons = []
    response.final_evidence_pack = EvidencePackRead()
    response.source_coverage_summary = {"chunk_count": 0, "fact_count": 0}
    return response


class FakeSession:
    pass


class FakeRetriever:
    def __init__(self, response: RetrievalResponse) -> None:
        self.response = response

    def retrieve(self, request):
        return self.response


class FakeProvider:
    def __init__(self, candidates: list[GeneratedAnswerCandidate]) -> None:
        self.candidates = candidates
        self.calls = 0
        self.prompts: list[str] = []

    def generate_candidate(self, prompt: str) -> GeneratedAnswerCandidate:
        self.prompts.append(prompt)
        candidate = self.candidates[self.calls]
        self.calls += 1
        return candidate

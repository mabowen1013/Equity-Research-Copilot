from datetime import date
from decimal import Decimal

from app.schemas import (
    CitationValidationIssueRead,
    MetricComparisonRead,
    RetrievalRequest,
)
from app.services.answer_generation import (
    answer_system_prompt,
    metric_comparison_record,
    normalize_generated_answer_citations,
)
from app.services import (
    CitationValidator,
    GeneratedAnswer,
    ResearchAnswerService,
    build_answer_evidence_context,
    build_prompt_evidence_records,
    extract_citation_markers,
)

from .test_answer_context import make_response


def test_citation_validator_passes_known_prompt_citation() -> None:
    context = make_context()
    prompt_ids = prompt_evidence_ids(context)
    generated = GeneratedAnswer(
        answer="Total net sales were supported by the retrieved filing fact. [financial_fact:501]",
        cited_evidence_ids=["financial_fact:501"],
    )

    validation = CitationValidator().validate(
        generated,
        allowed_evidence_ids=context.allowed_evidence_ids,
        prompt_evidence_ids=prompt_ids,
    )

    assert validation.status == "passed"
    assert validation.cited_evidence_ids == ["financial_fact:501"]
    assert validation.errors == []


def test_citation_validator_rejects_unknown_citation() -> None:
    context = make_context()
    generated = GeneratedAnswer(
        answer="Revenue was $111.2B. [chunk:999]",
        cited_evidence_ids=["chunk:999"],
    )

    validation = CitationValidator().validate(
        generated,
        allowed_evidence_ids=context.allowed_evidence_ids,
        prompt_evidence_ids=prompt_evidence_ids(context),
    )

    assert validation.status == "failed"
    assert validation.errors[0].code == "missing_valid_citations"
    assert validation.errors[0].evidence_id is None


def test_citation_validator_allows_uncited_sentences_when_answer_has_valid_source() -> None:
    context = make_context()
    generated = GeneratedAnswer(
        answer=(
            "Revenue was $111.2B. "
            "The retrieved span supports the reported amount. "
            "[span:101:primary_financial_statement_chunks:0:80]"
        ),
        cited_evidence_ids=["span:101:primary_financial_statement_chunks:0:80"],
    )

    validation = CitationValidator().validate(
        generated,
        allowed_evidence_ids=context.allowed_evidence_ids,
        prompt_evidence_ids=prompt_evidence_ids(context),
    )

    assert validation.status == "passed"
    assert validation.errors == []


def test_generated_answer_normalization_repairs_evidence_id_prefixed_markers() -> None:
    context = make_context()
    records = build_prompt_evidence_records(context)
    target_id = records[0].evidence_id
    generated = GeneratedAnswer(
        answer=f"Revenue was $111.2B. [evidence_id: {target_id}]",
        cited_evidence_ids=[f"evidence_id: {target_id}"],
    )

    normalized = normalize_generated_answer_citations(generated, records)

    assert f"[{target_id}]" in normalized.answer
    assert "evidence_id:" not in normalized.answer
    assert normalized.cited_evidence_ids == [target_id]


def test_citation_validator_reports_uncited_claim_sentences_as_warnings() -> None:
    context = make_context()
    generated = GeneratedAnswer(
        answer=(
            "Revenue was $111.2B. [financial_fact:501] "
            "Margins also improved across all segments."
        ),
        cited_evidence_ids=["financial_fact:501"],
    )

    validation = CitationValidator().validate(
        generated,
        allowed_evidence_ids=context.allowed_evidence_ids,
        prompt_evidence_ids=prompt_evidence_ids(context),
    )

    assert validation.status == "passed"
    assert validation.claim_sentence_count == 2
    assert validation.cited_claim_sentence_count == 1
    assert len(validation.warnings) == 1
    assert validation.warnings[0].code == "uncited_claim_sentence"
    assert "Margins also improved" in (validation.warnings[0].sentence or "")


def test_citation_validator_counts_fully_cited_claims_without_warnings() -> None:
    context = make_context()
    generated = GeneratedAnswer(
        answer=(
            "Revenue was $111.2B. [financial_fact:501] "
            "The retrieved span supports the reported amount. "
            "[span:101:primary_financial_statement_chunks:0:80]"
        ),
        cited_evidence_ids=["financial_fact:501"],
    )

    validation = CitationValidator().validate(
        generated,
        allowed_evidence_ids=context.allowed_evidence_ids,
        prompt_evidence_ids=prompt_evidence_ids(context),
    )

    assert validation.status == "passed"
    assert validation.claim_sentence_count == validation.cited_claim_sentence_count
    assert validation.warnings == []


def test_extract_citation_markers_handles_metric_comparison_ids() -> None:
    answer = (
        "Revenue improved year over year "
        "[metric_comparison:revenue:latest_quarter_yoy:10:9]."
    )

    assert extract_citation_markers(answer) == [
        "metric_comparison:revenue:latest_quarter_yoy:10:9"
    ]


def test_answer_prompt_discourages_generic_limitations() -> None:
    prompt = answer_system_prompt()

    assert "Do not add generic caveats" in prompt
    assert "margin changes in percentage points" in prompt


def test_metric_comparison_prompt_record_formats_margin_as_percentages() -> None:
    comparison = MetricComparisonRead(
        evidence_id="metric_comparison:gross_margin:latest_quarter_yoy:47518:47512",
        basis="latest_quarter_yoy",
        canonical_metric_key="gross_margin",
        current_fact_id=47518,
        prior_fact_id=47512,
        current_period_start=date(2026, 1, 1),
        current_period_end=date(2026, 3, 31),
        prior_period_start=date(2025, 1, 1),
        prior_period_end=date(2025, 3, 31),
        current_duration_class="quarter",
        prior_duration_class="quarter",
        current_period_label="Q2 2026 quarter",
        prior_period_label="Q2 2025 quarter",
        current_value=Decimal("0.49"),
        prior_value=Decimal("0.47"),
        growth_rate=Decimal("0.0472"),
        current_source_fiscal_year=2026,
        current_fact_fiscal_year=2026,
        prior_source_fiscal_year=2025,
        prior_fact_fiscal_year=2025,
        current_fiscal_period="Q2",
        prior_fiscal_period="Q2",
        current_source_filing_url="https://www.sec.gov/current.htm",
        prior_source_filing_url="https://www.sec.gov/prior.htm",
    )

    record = metric_comparison_record(comparison)

    assert "49% for Q2 2026 quarter" in record.text
    assert "47% for Q2 2025 quarter" in record.text
    assert "up 2 percentage points" in record.text
    assert "0.49" not in record.text


def test_generated_answer_normalization_repairs_numbered_citation_markers() -> None:
    context = make_context()
    records = build_prompt_evidence_records(context)
    generated = GeneratedAnswer(
        answer="Revenue grew because the filing text said so. [1]",
        cited_evidence_ids=["Source 1"],
    )

    normalized = normalize_generated_answer_citations(generated, records)

    assert normalized.answer == (
        f"Revenue grew because the filing text said so. [{records[0].evidence_id}]"
    )
    assert normalized.cited_evidence_ids == [records[0].evidence_id]


def test_generated_answer_normalization_removes_invalid_markers_and_appends_listed_source() -> None:
    context = make_context()
    records = build_prompt_evidence_records(context)
    generated = GeneratedAnswer(
        answer="Revenue grew because of regional sales strength. [chunk:999]",
        cited_evidence_ids=[records[0].evidence_id],
    )

    normalized = normalize_generated_answer_citations(generated, records)

    assert "[chunk:999]" not in normalized.answer
    assert normalized.answer.endswith(f"[{records[0].evidence_id}]")
    assert normalized.cited_evidence_ids == [records[0].evidence_id]


def test_research_answer_service_retries_once_after_validation_failure() -> None:
    generator = SequenceAnswerGenerator(
        [
            GeneratedAnswer(
                answer="This cites an invalid id. [chunk:999]",
                cited_evidence_ids=["chunk:999"],
            ),
            GeneratedAnswer(
                answer=(
                    "Total net sales were supported by the selected filing span. "
                    "[span:101:primary_financial_statement_chunks:0:80]"
                ),
                cited_evidence_ids=["span:101:primary_financial_statement_chunks:0:80"],
            ),
        ]
    )
    service = ResearchAnswerService(
        None,
        retriever=FakeRetriever(),
        answer_generator=generator,
    )

    response = service.answer(make_request())

    assert response.validation_status == "passed"
    assert response.validation.status == "passed"
    assert response.citations[0].evidence_id == "span:101:primary_financial_statement_chunks:0:80"
    assert generator.call_count == 2
    assert generator.validation_errors_seen[1][0].code == "missing_valid_citations"


def test_research_answer_service_accepts_repaired_numbered_citation() -> None:
    generator = SequenceAnswerGenerator(
        [
            GeneratedAnswer(
                answer="Revenue growth was supported by the selected evidence. [1]",
                cited_evidence_ids=["Source 1"],
            ),
        ]
    )
    service = ResearchAnswerService(
        None,
        retriever=FakeRetriever(),
        answer_generator=generator,
    )

    response = service.answer(make_request())

    assert response.validation_status == "passed"
    assert response.validation.errors == []
    assert response.citations[0].evidence_id == response.prompt_evidence_ids[0]
    assert response.answer.endswith(f"[{response.prompt_evidence_ids[0]}]")
    assert generator.call_count == 1


def test_research_answer_service_answers_from_existing_retrieval_response() -> None:
    generator = SequenceAnswerGenerator(
        [
            GeneratedAnswer(
                answer=(
                    "Total net sales were supported by the selected filing span. "
                    "[span:101:primary_financial_statement_chunks:0:80]"
                ),
                cited_evidence_ids=["span:101:primary_financial_statement_chunks:0:80"],
            ),
        ]
    )
    service = ResearchAnswerService(
        None,
        retriever=FakeRetriever(),
        answer_generator=generator,
    )

    response = service.answer_from_retrieval_response(make_request(), make_response())

    assert response.validation_status == "passed"
    assert response.retrieval_plan.question_type == "metric"
    assert generator.call_count == 1


def test_research_answer_service_uses_extractive_fallback_after_failed_retry() -> None:
    generator = SequenceAnswerGenerator(
        [
            GeneratedAnswer(
                answer="This cites an invalid id. [chunk:999]",
                cited_evidence_ids=["chunk:999"],
            ),
            GeneratedAnswer(
                answer="Still invalid. [chunk:999]",
                cited_evidence_ids=["chunk:999"],
            ),
        ]
    )
    service = ResearchAnswerService(
        None,
        retriever=FakeRetriever(),
        answer_generator=generator,
    )

    response = service.answer(make_request())

    assert response.validation_status == "passed"
    assert response.validation.status == "passed"
    assert response.citations
    assert response.answer != (
        "I do not have enough validated retrieved SEC evidence to answer the "
        "question for AAPL without risking an unsupported claim."
    )
    assert generator.call_count == 2


def test_research_answer_service_returns_insufficient_evidence_when_prompt_empty() -> None:
    service = ResearchAnswerService(
        None,
        retriever=FakeRetriever(response=make_empty_response()),
        answer_generator=SequenceAnswerGenerator([]),
    )

    response = service.answer(make_request())

    assert response.validation_status == "insufficient_evidence"
    assert response.prompt_evidence_ids == []
    assert response.validation.errors[0].code == "insufficient_evidence"


class SequenceAnswerGenerator:
    def __init__(self, answers: list[GeneratedAnswer]) -> None:
        self.answers = answers
        self.call_count = 0
        self.validation_errors_seen: list[list[CitationValidationIssueRead]] = []

    def generate(self, context, evidence_records, *, validation_errors=None):
        del context, evidence_records
        self.validation_errors_seen.append(validation_errors or [])
        answer = self.answers[self.call_count]
        self.call_count += 1
        return answer


class FakeRetriever:
    def __init__(self, response=None) -> None:
        self.response = response or make_response()

    def retrieve(self, request):
        del request
        return self.response


def make_request() -> RetrievalRequest:
    return RetrievalRequest(ticker="AAPL", question="What was latest revenue?")


def make_context():
    return build_answer_evidence_context(make_request(), make_response())


def prompt_evidence_ids(context) -> list[str]:
    return [record.evidence_id for record in build_prompt_evidence_records(context)]


def make_empty_response():
    response = make_response()
    response.retrieved_chunks = []
    response.retrieved_facts = []
    response.final_evidence_pack = response.final_evidence_pack.model_copy(
        update={
            "primary_financial_statement_chunks": [],
            "primary_financial_statement_spans": [],
        }
    )
    return response

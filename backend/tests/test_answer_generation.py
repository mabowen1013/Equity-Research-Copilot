from datetime import date
from decimal import Decimal

from app.core import Settings
from app.schemas import (
    CitationValidationIssueRead,
    MetricComparisonRead,
    RetrievalRequest,
)
from app.schemas.answer import AnswerCitationRead
from app.services.answer_generation import (
    AnswerStreamEmitter,
    PromptEvidenceRecord,
    answer_stream_system_prompt,
    answer_system_prompt,
    build_answer_prompt_payload,
    build_citation_alias_map,
    metric_comparison_record,
    normalize_generated_answer_citations,
    parse_entailment_labels,
    parse_salient_numbers,
    split_streamed_answer,
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


def test_answer_prompts_instruct_index_based_citations() -> None:
    assert "[index]" in answer_system_prompt()
    assert "[index]" in answer_stream_system_prompt()


def test_answer_prompt_payload_indexes_evidence_for_numbered_citations() -> None:
    context = make_context()
    records = build_prompt_evidence_records(context)
    payload = build_answer_prompt_payload(context, records)

    # Each evidence object carries a 1-based index the model is told to cite.
    indexes = [item["index"] for item in payload["evidence"]]
    assert indexes == list(range(1, len(records) + 1))

    # The index the model sees must resolve back to that record's evidence_id,
    # which is what makes a streamed "[1]" marker validate after normalization.
    alias_map = build_citation_alias_map(records)
    for item in payload["evidence"]:
        assert alias_map[str(item["index"])] == item["evidence_id"]


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
    span_citation = next(
        citation
        for citation in response.citations
        if citation.evidence_type == "evidence_span"
    )
    assert span_citation.source_ids["filing_id"] == 10
    assert span_citation.source_ids["chunk_id"] == 101


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


def test_split_streamed_answer_extracts_limitations_block() -> None:
    content = (
        "Revenue grew 8%. [financial_fact:501]\n"
        "LIMITATIONS:\n"
        "- Only one quarter of data was available.\n"
        "- No segment detail.\n"
    )

    answer, limitations = split_streamed_answer(content)

    assert answer == "Revenue grew 8%. [financial_fact:501]"
    assert limitations == [
        "Only one quarter of data was available.",
        "No segment detail.",
    ]


def test_split_streamed_answer_without_limitations_block() -> None:
    answer, limitations = split_streamed_answer("Revenue grew. [chunk:1]\n")

    assert answer == "Revenue grew. [chunk:1]"
    assert limitations == []


def test_answer_stream_emitter_withholds_limitations_block() -> None:
    deltas: list[str] = []
    emitter = AnswerStreamEmitter(deltas.append)

    for piece in [
        "Revenue grew 8%. ",
        "[financial_fact:501]",
        "\nLIMIT",
        "ATIONS:\n- Only one quarter.",
    ]:
        emitter.feed(piece)
    full_text = emitter.finish()

    streamed = "".join(deltas)
    assert streamed.strip() == "Revenue grew 8%. [financial_fact:501]"
    assert "LIMITATIONS" not in streamed
    assert "LIMITATIONS:" in full_text


def test_answer_stream_emitter_flushes_tail_without_sentinel() -> None:
    deltas: list[str] = []
    emitter = AnswerStreamEmitter(deltas.append)

    emitter.feed("Revenue grew 8%. [financial_fact:501]")
    full_text = emitter.finish()

    assert "".join(deltas) == full_text == "Revenue grew 8%. [financial_fact:501]"


def test_research_answer_service_emits_stream_events_when_on_event_provided() -> None:
    answer = (
        "Total net sales were supported by the selected filing span. "
        "[span:101:primary_financial_statement_chunks:0:80]"
    )
    generator = SequenceAnswerGenerator(
        [
            GeneratedAnswer(
                answer=answer,
                cited_evidence_ids=["span:101:primary_financial_statement_chunks:0:80"],
            ),
        ]
    )
    service = ResearchAnswerService(
        None,
        retriever=FakeRetriever(),
        answer_generator=generator,
    )
    events: list[dict] = []

    response = service.answer_from_retrieval_response(
        make_request(),
        make_response(),
        on_event=events.append,
    )

    assert response.validation_status == "passed"
    # Unvalidated answer text is never forwarded; only a status event signals
    # activity, and the validated answer reaches the caller via the response.
    assert [event["type"] for event in events] == ["status", "validation"]
    assert "answer_delta" not in [event["type"] for event in events]
    assert events[0]["stage"] == "answering"
    assert events[-1]["status"] == "passed"
    assert response.answer == answer


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


class FakeAnswerabilityJudge:
    def __init__(self, answerable: bool, reason: str = "") -> None:
        self._answerable = answerable
        self._reason = reason
        self.calls = 0

    def is_answerable(self, question, evidence_records):
        self.calls += 1
        return self._answerable, self._reason


def _passing_generator() -> "SequenceAnswerGenerator":
    return SequenceAnswerGenerator(
        [
            GeneratedAnswer(
                answer=(
                    "Total net sales were supported by the selected filing span. "
                    "[span:101:primary_financial_statement_chunks:0:80]"
                ),
                cited_evidence_ids=["span:101:primary_financial_statement_chunks:0:80"],
            )
        ]
    )


def test_answerability_gate_declines_unanswerable_question() -> None:
    judge = FakeAnswerabilityJudge(False, "evidence has no gross margin")
    service = ResearchAnswerService(
        None,
        retriever=FakeRetriever(),
        answer_generator=_passing_generator(),
        answerability_judge=judge,
    )

    response = service.answer(make_request())

    assert judge.calls == 1
    assert response.validation_status == "insufficient_evidence"
    assert response.validation.errors[0].code == "question_not_answerable"


def test_answerability_gate_allows_answerable_question() -> None:
    judge = FakeAnswerabilityJudge(True)
    service = ResearchAnswerService(
        None,
        retriever=FakeRetriever(),
        answer_generator=_passing_generator(),
        answerability_judge=judge,
    )

    response = service.answer(make_request())

    assert judge.calls == 1
    assert response.validation_status == "passed"


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


def make_number_record(evidence_id: str, text: str) -> PromptEvidenceRecord:
    return PromptEvidenceRecord(
        evidence_id=evidence_id,
        evidence_type="financial_fact",
        source_label="fact",
        text=text,
        citation=AnswerCitationRead(
            evidence_id=evidence_id,
            evidence_type="financial_fact",
        ),
    )


def test_parse_salient_numbers_extracts_amounts_and_percents() -> None:
    parsed = parse_salient_numbers(
        "Revenue was $111.18B, up 8.1%, and 2.3 percentage points in FY2024."
    )

    pairs = {(kind, value) for kind, value, _ in parsed}
    assert ("amount", Decimal("111.18e9")) in pairs
    assert ("percent", Decimal("8.1")) in pairs
    assert ("percent", Decimal("2.3")) in pairs
    # The bare year must not be treated as a financial claim.
    assert all(value != Decimal("2024") for _, value, _ in parsed)


def test_citation_validator_flags_unsupported_number() -> None:
    records = [
        make_number_record(
            "financial_fact:501", "Revenue was $111.18B for Q2 2026 quarter."
        )
    ]
    generated = GeneratedAnswer(
        answer="Revenue was $950B. [financial_fact:501]",
        cited_evidence_ids=["financial_fact:501"],
    )

    validation = CitationValidator().validate(
        generated,
        allowed_evidence_ids=["financial_fact:501"],
        prompt_evidence_ids=["financial_fact:501"],
        evidence_records=records,
    )

    assert validation.status == "passed"
    codes = {warning.code for warning in validation.warnings}
    assert "unsupported_number" in codes
    assert "citation_number_mismatch" not in codes


def test_citation_validator_flags_citation_number_mismatch() -> None:
    records = [
        make_number_record(
            "financial_fact:501", "Revenue was $111.18B for Q2 2026 quarter."
        ),
        make_number_record(
            "financial_fact:777", "Net Income was $24.16B for Q2 2026 quarter."
        ),
    ]
    generated = GeneratedAnswer(
        answer="Net income was $24.2B. [financial_fact:501]",
        cited_evidence_ids=["financial_fact:501"],
    )

    validation = CitationValidator().validate(
        generated,
        allowed_evidence_ids=["financial_fact:501", "financial_fact:777"],
        prompt_evidence_ids=["financial_fact:501", "financial_fact:777"],
        evidence_records=records,
    )

    assert validation.status == "passed"
    codes = {warning.code for warning in validation.warnings}
    assert "citation_number_mismatch" in codes
    assert "unsupported_number" not in codes


def test_citation_validator_accepts_rounded_supported_number() -> None:
    records = [
        make_number_record(
            "financial_fact:501", "Revenue was $111.18B for Q2 2026 quarter."
        )
    ]
    generated = GeneratedAnswer(
        answer="Revenue was $111.2B. [financial_fact:501]",
        cited_evidence_ids=["financial_fact:501"],
    )

    validation = CitationValidator().validate(
        generated,
        allowed_evidence_ids=["financial_fact:501"],
        prompt_evidence_ids=["financial_fact:501"],
        evidence_records=records,
    )

    assert validation.status == "passed"
    codes = {warning.code for warning in validation.warnings}
    assert "unsupported_number" not in codes
    assert "citation_number_mismatch" not in codes


def test_citation_validator_skips_number_checks_without_evidence_records() -> None:
    generated = GeneratedAnswer(
        answer="Revenue was $950B. [financial_fact:501]",
        cited_evidence_ids=["financial_fact:501"],
    )

    validation = CitationValidator().validate(
        generated,
        allowed_evidence_ids=["financial_fact:501"],
        prompt_evidence_ids=["financial_fact:501"],
    )

    codes = {warning.code for warning in validation.warnings}
    assert "unsupported_number" not in codes
    assert "citation_number_mismatch" not in codes


class FakeEntailmentJudge:
    def __init__(self, labels: list[str]) -> None:
        self._labels = labels
        self.calls = 0

    def judge(self, claims: list[dict]) -> list[str]:
        self.calls += 1
        return [self._labels[i] if i < len(self._labels) else "entailed" for i in range(len(claims))]


def _entail_records() -> list[PromptEvidenceRecord]:
    return [
        make_number_record(
            "financial_fact:501", "Revenue was $111.18B for the Q2 2026 quarter."
        )
    ]


def test_entailment_contradiction_fails_validation() -> None:
    generated = GeneratedAnswer(
        answer="Apple plans to discontinue the iPhone next year. [financial_fact:501]",
        cited_evidence_ids=["financial_fact:501"],
    )

    validation = CitationValidator(
        entailment_judge=FakeEntailmentJudge(["contradicted"])
    ).validate(
        generated,
        allowed_evidence_ids=["financial_fact:501"],
        prompt_evidence_ids=["financial_fact:501"],
        evidence_records=_entail_records(),
    )

    assert validation.status == "failed"
    assert "contradicted_claim" in {issue.code for issue in validation.errors}


def test_entailment_neutral_warns_without_failing() -> None:
    generated = GeneratedAnswer(
        answer="Apple is focused on its services strategy. [financial_fact:501]",
        cited_evidence_ids=["financial_fact:501"],
    )

    validation = CitationValidator(
        entailment_judge=FakeEntailmentJudge(["neutral"])
    ).validate(
        generated,
        allowed_evidence_ids=["financial_fact:501"],
        prompt_evidence_ids=["financial_fact:501"],
        evidence_records=_entail_records(),
    )

    assert validation.status == "passed"
    assert "unsupported_claim" in {issue.code for issue in validation.warnings}


def test_entailment_entailed_is_clean() -> None:
    judge = FakeEntailmentJudge(["entailed"])
    generated = GeneratedAnswer(
        answer="Revenue was strong this quarter. [financial_fact:501]",
        cited_evidence_ids=["financial_fact:501"],
    )

    validation = CitationValidator(entailment_judge=judge).validate(
        generated,
        allowed_evidence_ids=["financial_fact:501"],
        prompt_evidence_ids=["financial_fact:501"],
        evidence_records=_entail_records(),
    )

    assert judge.calls == 1
    codes = {issue.code for issue in [*validation.errors, *validation.warnings]}
    assert "contradicted_claim" not in codes
    assert "unsupported_claim" not in codes


def test_entailment_skipped_when_check_disabled() -> None:
    # Default settings keep the check off, so no judge runs even on a contradiction.
    generated = GeneratedAnswer(
        answer="Apple plans to discontinue the iPhone next year. [financial_fact:501]",
        cited_evidence_ids=["financial_fact:501"],
    )

    validation = CitationValidator(settings=Settings(_env_file=None)).validate(
        generated,
        allowed_evidence_ids=["financial_fact:501"],
        prompt_evidence_ids=["financial_fact:501"],
        evidence_records=_entail_records(),
    )

    assert validation.status == "passed"
    assert "contradicted_claim" not in {issue.code for issue in validation.errors}


def test_parse_entailment_labels_normalizes_and_pads() -> None:
    labels = parse_entailment_labels(
        '{"labels": ["Contradicted", {"label": "neutral"}, "bogus"]}', 4
    )

    assert labels == ["contradicted", "neutral", "entailed", "entailed"]

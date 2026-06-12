from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
import json
import re
from typing import Any, Protocol

from app.core import Settings, get_settings
from app.schemas.answer import (
    AnswerCitationRead,
    AnswerEvidenceContextRead,
    CitationValidationIssueRead,
    CitationValidationRead,
    ResearchAnswerResponseRead,
)
from app.schemas.retrieval import (
    EvidencePackRead,
    EvidenceSpanRead,
    MetricComparisonRead,
    MetricObservationComponentRead,
    MetricObservationRead,
    RetrievedChunkRead,
    RetrievedFinancialFactRead,
    RetrievalRequest,
    RetrievalResponse,
)
from app.services.answer_context import (
    build_answer_evidence_context,
    collect_answer_evidence_ids,
)
from app.services.openai_client import get_openai_client
from app.services.retrieval import RetrievalService

MAX_PROMPT_EVIDENCE_ITEMS = 24
MAX_PROMPT_TEXT_CHARS = 900
MAX_EXTRACTIVE_SENTENCES = 5
EVIDENCE_MARKER_RE = re.compile(
    r"\[((?:chunk|span|financial_fact|metric_observation|metric_comparison):[^\]\s]+)\]"
)
NUMBERED_CITATION_MARKER_RE = re.compile(r"\[(?:source\s*)?#?(\d{1,3})\]", re.IGNORECASE)
PREFIXED_EVIDENCE_MARKER_RE = re.compile(
    r"\[\s*evidence_id\s*:\s*((?:chunk|span|financial_fact|metric_observation|metric_comparison):[^\]\s]+)\s*\]",
    re.IGNORECASE,
)
NUMBERED_CITATION_VALUE_RE = re.compile(r"(?:source|citation)?\s*#?\s*(\d{1,3})", re.IGNORECASE)
SENTENCE_BOUNDARY_RE = re.compile(r"(?<!\d)[.!?](?!\d)")
RATIO_METRIC_KEYS = {"gross_margin", "operating_margin", "net_margin"}


class AnswerGenerationError(RuntimeError):
    """Raised when answer generation cannot produce a structured answer."""


@dataclass(frozen=True)
class GeneratedAnswer:
    answer: str
    cited_evidence_ids: list[str] = field(default_factory=list)
    limitations: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class PromptEvidenceRecord:
    evidence_id: str
    evidence_type: str
    source_label: str | None
    text: str
    citation: AnswerCitationRead

    def to_prompt_dict(self) -> dict[str, Any]:
        return {
            "evidence_id": self.evidence_id,
            "evidence_type": self.evidence_type,
            "source_label": self.source_label,
            "text": self.text,
            "sec_url": self.citation.sec_url,
            "form_type": self.citation.form_type,
            "filing_date": self.citation.filing_date,
            "section": self.citation.section,
            "pages": self.citation.pages,
            "source_ids": self.citation.source_ids,
        }


class AnswerGenerator(Protocol):
    def generate(
        self,
        context: AnswerEvidenceContextRead,
        evidence_records: list[PromptEvidenceRecord],
        *,
        validation_errors: list[CitationValidationIssueRead] | None = None,
    ) -> GeneratedAnswer:
        """Generate an answer that cites ids from evidence_records."""


class ExtractiveAnswerGenerator:
    """Deterministic fallback that summarizes the strongest retrieved evidence."""

    def generate(
        self,
        context: AnswerEvidenceContextRead,
        evidence_records: list[PromptEvidenceRecord],
        *,
        validation_errors: list[CitationValidationIssueRead] | None = None,
    ) -> GeneratedAnswer:
        del validation_errors
        if not evidence_records:
            return GeneratedAnswer(
                answer=insufficient_evidence_answer(context.ticker),
                limitations=["No prompt evidence was available for answer generation."],
            )

        selected = select_extractive_records(evidence_records)
        sentences = [
            format_extractive_sentence(record)
            for record in selected[:MAX_EXTRACTIVE_SENTENCES]
        ]
        answer = " ".join(sentence for sentence in sentences if sentence).strip()
        cited_ids = extract_citation_markers(answer)
        return GeneratedAnswer(
            answer=answer or insufficient_evidence_answer(context.ticker),
            cited_evidence_ids=cited_ids,
            limitations=["Generated from retrieved SEC evidence only."],
        )


class OpenAIAnswerGenerator:
    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()

    def generate(
        self,
        context: AnswerEvidenceContextRead,
        evidence_records: list[PromptEvidenceRecord],
        *,
        validation_errors: list[CitationValidationIssueRead] | None = None,
    ) -> GeneratedAnswer:
        api_key = self._settings.openai_api_key
        if api_key is None or not api_key.get_secret_value().strip():
            raise AnswerGenerationError("OPENAI_API_KEY must be configured for answer generation.")

        try:
            client = get_openai_client(
                api_key.get_secret_value(),
                timeout=self._settings.answer_llm_timeout_seconds,
                max_retries=self._settings.answer_llm_max_retries,
            )
        except ImportError as exc:
            raise AnswerGenerationError(
                "The openai package must be installed for answer generation."
            ) from exc

        payload = build_answer_prompt_payload(
            context,
            evidence_records,
            validation_errors=validation_errors,
        )
        response = client.chat.completions.create(
            model=self._settings.answer_llm_model,
            temperature=0,
            max_tokens=self._settings.answer_llm_max_output_tokens,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": answer_system_prompt()},
                {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
            ],
        )
        content = response.choices[0].message.content or "{}"
        return parse_generated_answer(content)


class FallbackAnswerGenerator:
    def __init__(
        self,
        primary: AnswerGenerator,
        fallback: AnswerGenerator,
    ) -> None:
        self._primary = primary
        self._fallback = fallback

    def generate(
        self,
        context: AnswerEvidenceContextRead,
        evidence_records: list[PromptEvidenceRecord],
        *,
        validation_errors: list[CitationValidationIssueRead] | None = None,
    ) -> GeneratedAnswer:
        try:
            return self._primary.generate(
                context,
                evidence_records,
                validation_errors=validation_errors,
            )
        except AnswerGenerationError as exc:
            fallback_answer = self._fallback.generate(
                context,
                evidence_records,
                validation_errors=validation_errors,
            )
            return GeneratedAnswer(
                answer=fallback_answer.answer,
                cited_evidence_ids=fallback_answer.cited_evidence_ids,
                limitations=[
                    *fallback_answer.limitations,
                    f"LLM answer generation unavailable; used extractive fallback ({exc}).",
                ],
            )


class CitationValidator:
    def validate(
        self,
        generated: GeneratedAnswer,
        *,
        allowed_evidence_ids: list[str],
        prompt_evidence_ids: list[str],
    ) -> CitationValidationRead:
        allowed_set = set(allowed_evidence_ids)
        prompt_set = set(prompt_evidence_ids)
        marker_ids = extract_citation_markers(generated.answer)
        listed_ids = normalize_cited_evidence_ids(generated.cited_evidence_ids)
        cited_ids = list(dict.fromkeys([*marker_ids, *listed_ids]))
        valid_cited_ids = [
            evidence_id
            for evidence_id in cited_ids
            if evidence_id in allowed_set and evidence_id in prompt_set
        ]
        errors: list[CitationValidationIssueRead] = []

        if not generated.answer.strip():
            errors.append(
                CitationValidationIssueRead(
                    code="empty_answer",
                    message="Generated answer was empty.",
                )
            )

        if generated.answer.strip() and not valid_cited_ids:
            errors.append(
                CitationValidationIssueRead(
                    code="missing_valid_citations",
                    message="Generated answer did not cite any prompt evidence.",
                )
            )

        warnings, claim_count, cited_claim_count = claim_citation_coverage(
            generated.answer,
            valid_evidence_ids=allowed_set & prompt_set,
        )

        return CitationValidationRead(
            status="failed" if errors else "passed",
            cited_evidence_ids=list(dict.fromkeys(valid_cited_ids)),
            allowed_evidence_ids=allowed_evidence_ids,
            prompt_evidence_ids=prompt_evidence_ids,
            errors=errors,
            warnings=warnings,
            claim_sentence_count=claim_count,
            cited_claim_sentence_count=cited_claim_count,
        )


class ResearchAnswerService:
    def __init__(
        self,
        db,
        *,
        settings: Settings | None = None,
        retriever=None,
        answer_generator: AnswerGenerator | None = None,
        validator: CitationValidator | None = None,
    ) -> None:
        self._settings = settings or get_settings()
        self._retriever = retriever or RetrievalService(db, settings=self._settings)
        self._answer_generator = answer_generator or build_answer_generator(self._settings)
        self._validator = validator or CitationValidator()

    def answer(self, request: RetrievalRequest) -> ResearchAnswerResponseRead:
        retrieval_response = RetrievalResponse.model_validate(self._retriever.retrieve(request))
        return self.answer_from_retrieval_response(request, retrieval_response)

    def answer_from_retrieval_response(
        self,
        request: RetrievalRequest,
        retrieval_response: RetrievalResponse,
    ) -> ResearchAnswerResponseRead:
        context = build_answer_evidence_context(request, retrieval_response)
        evidence_records = build_prompt_evidence_records(context)
        prompt_evidence_ids = [record.evidence_id for record in evidence_records]
        retrieved_evidence_ids = collect_retrieved_evidence_ids(retrieval_response)

        if not prompt_evidence_ids:
            return build_insufficient_evidence_response(
                context,
                retrieval_response,
                retrieved_evidence_ids=retrieved_evidence_ids,
                prompt_evidence_ids=prompt_evidence_ids,
                errors=[
                    CitationValidationIssueRead(
                        code="insufficient_evidence",
                        message="No answer evidence was selected by retrieval.",
                    )
                ],
            )

        validation: CitationValidationRead | None = None
        generated: GeneratedAnswer | None = None
        for _ in range(2):
            try:
                generated = self._answer_generator.generate(
                    context,
                    evidence_records,
                    validation_errors=validation.errors if validation else None,
                )
            except AnswerGenerationError as exc:
                return build_insufficient_evidence_response(
                    context,
                    retrieval_response,
                    retrieved_evidence_ids=retrieved_evidence_ids,
                    prompt_evidence_ids=prompt_evidence_ids,
                    errors=[
                        CitationValidationIssueRead(
                            code="answer_generation_unavailable",
                            message=str(exc),
                        )
                    ],
                )

            generated = normalize_generated_answer_citations(generated, evidence_records)
            validation = self._validator.validate(
                generated,
                allowed_evidence_ids=context.allowed_evidence_ids,
                prompt_evidence_ids=prompt_evidence_ids,
            )
            if validation.status == "passed":
                return build_validated_answer_response(
                    generated,
                    validation,
                    context,
                    retrieved_evidence_ids=retrieved_evidence_ids,
                    prompt_evidence_ids=prompt_evidence_ids,
                )

        fallback_generated = normalize_generated_answer_citations(
            ExtractiveAnswerGenerator().generate(context, evidence_records),
            evidence_records,
        )
        fallback_validation = self._validator.validate(
            fallback_generated,
            allowed_evidence_ids=context.allowed_evidence_ids,
            prompt_evidence_ids=prompt_evidence_ids,
        )
        if fallback_validation.status == "passed":
            return build_validated_answer_response(
                fallback_generated,
                fallback_validation,
                context,
                retrieved_evidence_ids=retrieved_evidence_ids,
                prompt_evidence_ids=prompt_evidence_ids,
            )

        return build_insufficient_evidence_response(
            context,
            retrieval_response,
            retrieved_evidence_ids=retrieved_evidence_ids,
            prompt_evidence_ids=prompt_evidence_ids,
            errors=validation.errors if validation else [],
            limitations=["Citation validation failed for the generated answer."],
        )


def build_answer_generator(settings: Settings | None = None) -> AnswerGenerator:
    active_settings = settings or get_settings()
    if active_settings.answer_generator_mode == "extractive":
        return ExtractiveAnswerGenerator()
    if active_settings.answer_generator_mode == "llm":
        return OpenAIAnswerGenerator(active_settings)
    if (
        active_settings.openai_api_key is None
        or not active_settings.openai_api_key.get_secret_value().strip()
    ):
        return ExtractiveAnswerGenerator()
    return FallbackAnswerGenerator(
        OpenAIAnswerGenerator(active_settings),
        ExtractiveAnswerGenerator(),
    )


def build_answer_prompt_payload(
    context: AnswerEvidenceContextRead,
    evidence_records: list[PromptEvidenceRecord],
    *,
    validation_errors: list[CitationValidationIssueRead] | None = None,
) -> dict[str, Any]:
    return {
        "ticker": context.ticker,
        "question": context.question,
        "retrieval_plan": context.retrieval_plan.model_dump(mode="json"),
        "evidence": [record.to_prompt_dict() for record in evidence_records],
        "citation_format": "Append citations as [evidence_id] markers.",
        "validation_errors_to_fix": [
            issue.model_dump(mode="json") for issue in (validation_errors or [])
        ],
    }


def answer_system_prompt() -> str:
    return """
You are Equity Research Copilot, a citation-first research assistant for SEC filings.
Answer only from the evidence objects in the user payload. Do not use outside facts.
Do not invent citation ids. Use citation markers in the exact form [evidence_id].

Citation rules:
- Put citation markers after the sentence or bullet they support.
- A paragraph may contain multiple sentences, but each paragraph should include citations for its main claims.
- If one sentence summarizes multiple pieces of evidence, include multiple citation markers.
- Do not cite limitations unless the limitation itself comes from evidence.

Answer style:
- Do not give a one-sentence answer unless the evidence only supports one sentence.
- For simple metric questions, write 3-5 sentences.
- For change, why, comparison, or performance overview questions, write 5-8 sentences or 3-5 compact bullets.
- Start with a direct takeaway.
- Then include key numbers: current period, comparison period, and growth/change when available.
- Then explain drivers or context using MD&A/text evidence when available.
- Use readable financial formatting: revenue in $B/$M, margins as percentages, and margin changes in percentage points.
- Do not provide investment advice, price targets, ratings, or recommendations.

Return one JSON object with:
- answer: complete analyst-style answer string with citation markers.
- citations: array of evidence_id strings used as answer markers.
- limitations: array of short limitations or caveats.

Only include limitations for specific evidence gaps, conflicts, stale data, or unanswered parts of the question.
Do not add generic caveats.
If evidence is not enough, say so plainly in answer and keep citations empty.
""".strip()


def parse_generated_answer(content: str) -> GeneratedAnswer:
    try:
        payload = json.loads(content)
    except json.JSONDecodeError as exc:
        raise AnswerGenerationError("Answer generator returned invalid JSON.") from exc
    if not isinstance(payload, dict):
        raise AnswerGenerationError("Answer generator must return a JSON object.")
    answer = str(payload.get("answer", "")).strip()
    citations = payload.get("citations", [])
    limitations = payload.get("limitations", [])
    if not isinstance(citations, list):
        citations = []
    if not isinstance(limitations, list):
        limitations = []
    return GeneratedAnswer(
        answer=answer,
        cited_evidence_ids=normalize_cited_evidence_ids(citations),
        limitations=[
            str(limitation).strip()
            for limitation in limitations
            if str(limitation).strip()
        ],
    )


def normalize_generated_answer_citations(
    generated: GeneratedAnswer,
    evidence_records: list[PromptEvidenceRecord],
) -> GeneratedAnswer:
    alias_map = build_citation_alias_map(evidence_records)
    prompt_evidence_ids = {record.evidence_id for record in evidence_records}

    def replace_numbered_marker(match: re.Match[str]) -> str:
        evidence_id = alias_map.get(match.group(1).strip().lower())
        return f"[{evidence_id}]" if evidence_id is not None else match.group(0)

    answer = PREFIXED_EVIDENCE_MARKER_RE.sub(
        lambda match: f"[{match.group(1)}]",
        generated.answer,
    )
    answer = NUMBERED_CITATION_MARKER_RE.sub(
        replace_numbered_marker,
        answer,
    )
    answer = remove_invalid_citation_markers(answer, prompt_evidence_ids)
    marker_ids = extract_citation_markers(answer)
    resolved_listed_ids = [
        evidence_id
        for evidence_id in (
            resolve_citation_reference(value, alias_map)
            for value in generated.cited_evidence_ids
        )
        if evidence_id is not None and evidence_id in prompt_evidence_ids
    ]
    if not marker_ids and resolved_listed_ids and answer.strip():
        answer = f"{answer.rstrip()} {format_citation_markers(resolved_listed_ids)}"
        marker_ids = extract_citation_markers(answer)
    cited_ids = marker_ids or resolved_listed_ids
    return GeneratedAnswer(
        answer=answer,
        cited_evidence_ids=list(dict.fromkeys(cited_ids)),
        limitations=generated.limitations,
    )


def remove_invalid_citation_markers(
    answer: str,
    prompt_evidence_ids: set[str],
) -> str:
    def replace_marker(match: re.Match[str]) -> str:
        evidence_id = match.group(1)
        return match.group(0) if evidence_id in prompt_evidence_ids else ""

    return EVIDENCE_MARKER_RE.sub(replace_marker, answer)


def format_citation_markers(evidence_ids: list[str]) -> str:
    return "".join(f"[{evidence_id}]" for evidence_id in dict.fromkeys(evidence_ids))


def build_citation_alias_map(
    evidence_records: list[PromptEvidenceRecord],
) -> dict[str, str]:
    aliases: dict[str, str] = {}
    for index, record in enumerate(evidence_records, start=1):
        aliases[record.evidence_id.lower()] = record.evidence_id
        aliases[str(index)] = record.evidence_id
        aliases[f"source {index}"] = record.evidence_id
        aliases[f"source #{index}"] = record.evidence_id
        aliases[f"citation {index}"] = record.evidence_id
        aliases[f"citation #{index}"] = record.evidence_id
    return aliases


def resolve_citation_reference(
    value: str,
    alias_map: dict[str, str],
) -> str | None:
    cleaned = value.strip().strip("[]").strip()
    cleaned = re.sub(r"(?i)^evidence_id\s*:\s*", "", cleaned)
    normalized = cleaned.lower()
    if not normalized:
        return None
    if normalized in alias_map:
        return alias_map[normalized]
    match = NUMBERED_CITATION_VALUE_RE.fullmatch(normalized)
    if match is not None:
        return alias_map.get(match.group(1))
    return cleaned


def build_prompt_evidence_records(
    context: AnswerEvidenceContextRead,
) -> list[PromptEvidenceRecord]:
    return build_answer_evidence_records(
        context,
        max_items=MAX_PROMPT_EVIDENCE_ITEMS,
    )


def build_answer_evidence_records(
    context: AnswerEvidenceContextRead,
    *,
    max_items: int | None = None,
) -> list[PromptEvidenceRecord]:
    records: list[PromptEvidenceRecord] = []
    pack = context.final_evidence_pack

    for observation in pack.metric_observations:
        records.append(metric_observation_record(observation))
        for component in observation.component_observations:
            records.append(metric_observation_component_record(component))
    for comparison in pack.metric_comparisons:
        records.append(metric_comparison_record(comparison))

    for span in evidence_spans_for_answer(pack):
        records.append(evidence_span_record(span))
    for chunk in evidence_chunks_for_answer(pack):
        records.append(retrieved_chunk_record(chunk))
    for fact in context.retrieved_facts:
        records.append(retrieved_fact_record(fact))

    deduped = list({record.evidence_id: record for record in records}.values())
    if max_items is not None:
        return deduped[:max_items]
    return deduped


def build_answer_citation_index(
    context: AnswerEvidenceContextRead,
) -> dict[str, AnswerCitationRead]:
    return {
        record.evidence_id: record.citation
        for record in build_answer_evidence_records(context)
    }


def metric_observation_record(
    observation: MetricObservationRead,
) -> PromptEvidenceRecord:
    label = observation.canonical_metric_key.replace("_", " ").title()
    period = format_period(
        observation.period_end.isoformat(),
        observation.duration_class,
        observation.fiscal_period,
    )
    display_value = format_metric_answer_value(
        observation.canonical_metric_key,
        observation.value,
        observation.unit,
    )
    text = f"{label} was {display_value} for {period}."
    return PromptEvidenceRecord(
        evidence_id=observation.evidence_id,
        evidence_type="metric_observation",
        source_label=label,
        text=text,
        citation=AnswerCitationRead(
            evidence_id=observation.evidence_id,
            evidence_type="metric_observation",
            source_label=label,
            text=text,
            sec_url=observation.source_filing_url,
            form_type=observation.form_type,
            filing_date=date_to_str(observation.filed_date),
            section=None,
            pages=None,
            source_ids={
                "fact_id": observation.source_fact_id,
                "source_filing_id": observation.source_filing_id,
                "source_accession_number": observation.source_accession_number,
                "source_fact_evidence_id": observation.source_fact_evidence_id,
                "component_fact_ids": observation.component_fact_ids,
            },
        ),
    )


def metric_observation_component_record(
    component: MetricObservationComponentRead,
) -> PromptEvidenceRecord:
    label = component.canonical_metric_key.replace("_", " ").title()
    period = format_period(
        component.period_end.isoformat(),
        component.duration_class,
        component.fiscal_period,
    )
    text = f"{label} component was {component.display_value} for {period}."
    return PromptEvidenceRecord(
        evidence_id=component.evidence_id,
        evidence_type="financial_fact",
        source_label=label,
        text=text,
        citation=AnswerCitationRead(
            evidence_id=component.evidence_id,
            evidence_type="financial_fact",
            source_label=label,
            text=text,
            sec_url=component.source_filing_url,
            form_type=component.form_type,
            filing_date=date_to_str(component.filed_date),
            section=None,
            pages=None,
            source_ids={
                "fact_id": component.fact_id,
                "source_filing_id": component.source_filing_id,
                "source_accession_number": component.source_accession_number,
                "source_fact_id": component.source_fact_id,
            },
        ),
    )


def metric_comparison_record(
    comparison: MetricComparisonRead,
) -> PromptEvidenceRecord:
    label = comparison.canonical_metric_key.replace("_", " ").title()
    change_phrase = format_metric_change_phrase(
        comparison.canonical_metric_key,
        comparison.current_value,
        comparison.prior_value,
        comparison.growth_rate,
    )
    text = (
        f"{label} comparison ({comparison.basis}): "
        f"{format_metric_answer_value(comparison.canonical_metric_key, comparison.current_value)} for "
        f"{comparison.current_period_label or comparison.current_period_end.isoformat()} "
        f"vs {format_metric_answer_value(comparison.canonical_metric_key, comparison.prior_value)} for "
        f"{comparison.prior_period_label or comparison.prior_period_end.isoformat()}"
        f"{change_phrase}."
    )
    return PromptEvidenceRecord(
        evidence_id=comparison.evidence_id,
        evidence_type="metric_comparison",
        source_label=f"{label} {comparison.basis}",
        text=text,
        citation=AnswerCitationRead(
            evidence_id=comparison.evidence_id,
            evidence_type="metric_comparison",
            source_label=f"{label} {comparison.basis}",
            text=text,
            sec_url=comparison.current_source_filing_url,
            form_type=None,
            filing_date=comparison.current_period_end.isoformat(),
            section=None,
            pages=None,
            source_ids={
                "current_fact_id": comparison.current_fact_id,
                "prior_fact_id": comparison.prior_fact_id,
                "basis": comparison.basis,
                "current_source_filing_url": comparison.current_source_filing_url,
                "prior_source_filing_url": comparison.prior_source_filing_url,
            },
        ),
    )


def evidence_span_record(span: EvidenceSpanRead) -> PromptEvidenceRecord:
    source_label = span.support_kind.replace("_", " ").title()
    return PromptEvidenceRecord(
        evidence_id=span.evidence_id,
        evidence_type="evidence_span",
        source_label=source_label,
        text=truncate_prompt_text(span.text),
        citation=AnswerCitationRead(
            evidence_id=span.evidence_id,
            evidence_type="evidence_span",
            source_label=source_label,
            text=truncate_prompt_text(span.text),
            sec_url=span.sec_url,
            form_type=span.form_type,
            filing_date=span.filing_date.isoformat(),
            section=span.section_label,
            pages=format_pages(span.start_page, span.end_page),
            source_ids={
                "chunk_id": span.chunk_id,
                "source_chunk_evidence_id": span.source_chunk_evidence_id,
                "accession_number": span.accession_number,
                "start_char": span.start_char,
                "end_char": span.end_char,
            },
        ),
    )


def retrieved_chunk_record(chunk: RetrievedChunkRead) -> PromptEvidenceRecord:
    return PromptEvidenceRecord(
        evidence_id=chunk.evidence_id,
        evidence_type="chunk",
        source_label=chunk.section_label,
        text=truncate_prompt_text(chunk.snippet),
        citation=AnswerCitationRead(
            evidence_id=chunk.evidence_id,
            evidence_type="chunk",
            source_label=chunk.section_label,
            text=truncate_prompt_text(chunk.snippet),
            sec_url=chunk.sec_url,
            form_type=chunk.form_type,
            filing_date=chunk.filing_date.isoformat(),
            section=chunk.section_label,
            pages=format_pages(chunk.start_page, chunk.end_page),
            source_ids={
                "chunk_id": chunk.chunk_id,
                "filing_id": chunk.filing_id,
                "section_id": chunk.section_id,
                "accession_number": chunk.accession_number,
            },
        ),
    )


def retrieved_fact_record(fact: RetrievedFinancialFactRead) -> PromptEvidenceRecord:
    label = fact.label or fact.canonical_metric_key.replace("_", " ").title()
    period = fact.period_label or format_period(
        fact.period_end.isoformat(),
        fact.duration_class,
        fact.fiscal_period,
    )
    display_value = format_metric_answer_value(
        fact.canonical_metric_key,
        fact.value,
        fact.unit,
    )
    text = f"{label} was {display_value} for {period}."
    if fact.is_computed and fact.calculation_expression:
        text = f"{text} Computed as {fact.calculation_expression}."
    return PromptEvidenceRecord(
        evidence_id=fact.evidence_id,
        evidence_type="financial_fact",
        source_label=label,
        text=text,
        citation=AnswerCitationRead(
            evidence_id=fact.evidence_id,
            evidence_type="financial_fact",
            source_label=label,
            text=text,
            sec_url=fact.source_filing_url,
            form_type=fact.form_type,
            filing_date=date_to_str(fact.filed_date),
            section=None,
            pages=None,
            source_ids={
                "fact_id": fact.fact_id,
                "source_filing_id": fact.source_filing_id,
                "source_accession_number": fact.source_accession_number,
                "source_fact_id": fact.source_fact_id,
                "component_fact_ids": fact.component_fact_ids,
            },
        ),
    )


def evidence_spans_for_answer(pack: EvidencePackRead) -> list[EvidenceSpanRead]:
    return [
        *pack.primary_financial_statement_spans,
        *pack.mda_explanation_spans,
        *pack.segment_or_product_breakdown_spans,
        *pack.risk_factor_spans,
        *pack.annual_context_spans,
    ]


def evidence_chunks_for_answer(pack: EvidencePackRead) -> list[RetrievedChunkRead]:
    return [
        *pack.primary_financial_statement_chunks,
        *pack.mda_explanation_chunks,
        *pack.segment_or_product_breakdown_chunks,
        *pack.risk_factor_chunks,
        *pack.annual_context_chunks,
    ]


def collect_retrieved_evidence_ids(response: RetrievalResponse) -> list[str]:
    ids = [
        *(chunk.evidence_id for chunk in response.retrieved_chunks),
        *(fact.evidence_id for fact in response.retrieved_facts),
        *(comparison.evidence_id for comparison in response.metric_comparisons),
        *collect_answer_evidence_ids(response),
    ]
    return list(dict.fromkeys(ids))


def build_validated_citations(
    validation: CitationValidationRead,
    citation_index: dict[str, AnswerCitationRead],
) -> list[AnswerCitationRead]:
    citations: list[AnswerCitationRead] = []
    for evidence_id in validation.cited_evidence_ids:
        citation = citation_index.get(evidence_id)
        if citation is not None:
            citations.append(citation)
    return citations


def build_validated_answer_response(
    generated: GeneratedAnswer,
    validation: CitationValidationRead,
    context: AnswerEvidenceContextRead,
    *,
    retrieved_evidence_ids: list[str],
    prompt_evidence_ids: list[str],
) -> ResearchAnswerResponseRead:
    citation_index = build_answer_citation_index(context)
    return ResearchAnswerResponseRead(
        answer=generated.answer,
        citations=build_validated_citations(validation, citation_index),
        retrieved_evidence_ids=retrieved_evidence_ids,
        prompt_evidence_ids=prompt_evidence_ids,
        validation_status="passed",
        validation=validation,
        limitations=list(dict.fromkeys(generated.limitations)),
        source_coverage_summary=context.source_coverage_summary,
        retrieval_plan=context.retrieval_plan,
        final_evidence_pack=context.final_evidence_pack,
    )


def build_insufficient_evidence_response(
    context: AnswerEvidenceContextRead,
    retrieval_response: RetrievalResponse,
    *,
    retrieved_evidence_ids: list[str],
    prompt_evidence_ids: list[str],
    errors: list[CitationValidationIssueRead],
    limitations: list[str] | None = None,
) -> ResearchAnswerResponseRead:
    validation = CitationValidationRead(
        status="failed",
        cited_evidence_ids=[],
        allowed_evidence_ids=context.allowed_evidence_ids,
        prompt_evidence_ids=prompt_evidence_ids,
        errors=errors,
    )
    return ResearchAnswerResponseRead(
        answer=insufficient_evidence_answer(context.ticker),
        citations=[],
        retrieved_evidence_ids=retrieved_evidence_ids,
        prompt_evidence_ids=prompt_evidence_ids,
        validation_status="insufficient_evidence",
        validation=validation,
        limitations=list(
            dict.fromkeys(
                [
                    *(limitations or []),
                    "The system returned a safe fallback instead of an unsupported answer.",
                ]
            )
        ),
        source_coverage_summary=context.source_coverage_summary,
        retrieval_plan=context.retrieval_plan,
        final_evidence_pack=retrieval_response.final_evidence_pack,
    )


def insufficient_evidence_answer(ticker: str) -> str:
    return (
        f"I do not have enough validated retrieved SEC evidence to answer the "
        f"question for {ticker} without risking an unsupported claim."
    )


def select_extractive_records(
    evidence_records: list[PromptEvidenceRecord],
) -> list[PromptEvidenceRecord]:
    priority = {
        "metric_comparison": 0,
        "metric_observation": 1,
        "financial_fact": 2,
        "evidence_span": 3,
        "chunk": 4,
    }
    return sorted(
        evidence_records,
        key=lambda record: priority.get(record.evidence_type, 9),
    )


def format_extractive_sentence(record: PromptEvidenceRecord) -> str:
    text = record.text.strip()
    if not text:
        return ""
    text = text.rstrip()
    if text[-1:] not in ".!?":
        text = f"{text}."
    return f"{text} [{record.evidence_id}]"


def extract_citation_markers(answer: str) -> list[str]:
    return list(dict.fromkeys(EVIDENCE_MARKER_RE.findall(answer or "")))


def normalize_cited_evidence_ids(values: list[Any]) -> list[str]:
    normalized: list[str] = []
    for value in values:
        if isinstance(value, dict):
            value = value.get("evidence_id", "")
        text_value = str(value).strip().strip("[]")
        if not text_value:
            continue
        normalized.append(text_value)
    return list(dict.fromkeys(normalized))


def claim_citation_coverage(
    answer: str,
    *,
    valid_evidence_ids: set[str],
) -> tuple[list[CitationValidationIssueRead], int, int]:
    """Report claim sentences that carry no valid citation marker.

    Coverage gaps are warnings, not errors: a paragraph-level citation can still
    support adjacent sentences, so an uncited sentence is a quality signal for
    evals and the trace viewer rather than a reason to reject the answer.
    """
    warnings: list[CitationValidationIssueRead] = []
    claim_count = 0
    cited_claim_count = 0
    for sentence in answer_claim_sentences(answer):
        if not sentence_requires_citation(sentence):
            continue
        claim_count += 1
        sentence_ids = set(extract_citation_markers(sentence))
        if sentence_ids & valid_evidence_ids:
            cited_claim_count += 1
        else:
            warnings.append(
                CitationValidationIssueRead(
                    code="uncited_claim_sentence",
                    message="Claim sentence has no valid citation marker.",
                    sentence=sentence,
                )
            )
    return warnings, claim_count, cited_claim_count


def answer_claim_sentences(answer: str) -> list[str]:
    sentences: list[str] = []
    for line in (answer or "").splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        start = 0
        for match in SENTENCE_BOUNDARY_RE.finditer(stripped):
            end = consume_following_citation_markers(stripped, match.end())
            sentence = stripped[start:end].strip()
            if sentence:
                sentences.append(sentence)
            start = end
        tail = stripped[start:].strip()
        if tail:
            sentences.append(tail)
    return sentences


def consume_following_citation_markers(text: str, cursor: int) -> int:
    while True:
        match = re.match(r"\s*\[[^\]]+\]", text[cursor:])
        if match is None:
            return cursor
        cursor += match.end()


def sentence_requires_citation(sentence: str) -> bool:
    without_markers = EVIDENCE_MARKER_RE.sub("", sentence).strip()
    if not without_markers:
        return False
    lower = without_markers.lower()
    if lower.startswith(("limitation", "limitations", "caveat", "caveats")):
        return False
    return re.search(r"[A-Za-z]", without_markers) is not None


def format_pages(start_page: int | None, end_page: int | None) -> str | None:
    if start_page is None and end_page is None:
        return None
    if start_page == end_page or end_page is None:
        return str(start_page)
    if start_page is None:
        return str(end_page)
    return f"{start_page}-{end_page}"


def date_to_str(value: Any) -> str | None:
    return value.isoformat() if value is not None else None


def format_period(
    period_end: str,
    duration_class: str | None,
    fiscal_period: str | None,
) -> str:
    parts = [part for part in (fiscal_period, duration_class, period_end) if part]
    return " ".join(parts)


def format_metric_answer_value(
    metric_key: str,
    value: Decimal,
    unit: str = "",
) -> str:
    if metric_key in RATIO_METRIC_KEYS:
        return format_percent(value)
    return format_fact_value(value, unit)


def format_metric_change_phrase(
    metric_key: str,
    current_value: Decimal,
    prior_value: Decimal,
    growth_rate: Decimal | None,
) -> str:
    if metric_key in RATIO_METRIC_KEYS:
        point_change = (current_value - prior_value) * Decimal("100")
        if point_change == 0:
            return ", unchanged in percentage-point terms"
        direction = "up" if point_change > 0 else "down"
        return f", {direction} {format_decimal(abs(point_change))} percentage points"
    if growth_rate is None:
        return ""
    direction = "up" if growth_rate > 0 else "down" if growth_rate < 0 else "flat"
    if direction == "flat":
        return ", flat year over year"
    return f", {direction} {format_percent(abs(growth_rate))}"


def format_fact_value(value: Decimal, unit: str) -> str:
    prefix = "$" if unit.upper() in {"USD", "US_DOLLAR", "USDOLLARS"} else ""
    abs_value = abs(value)
    if abs_value >= Decimal("1000000000"):
        return f"{prefix}{format_decimal(value / Decimal('1000000000'))}B"
    if abs_value >= Decimal("1000000"):
        return f"{prefix}{format_decimal(value / Decimal('1000000'))}M"
    return f"{prefix}{format_decimal(value)} {unit}".strip()


def format_decimal(value: Decimal) -> str:
    quantized = value.quantize(Decimal("0.01"))
    return f"{quantized:,.2f}".rstrip("0").rstrip(".")


def format_percent(value: Decimal) -> str:
    return f"{format_decimal(value * Decimal('100'))}%"


def truncate_prompt_text(text: str) -> str:
    normalized = " ".join(text.split())
    if len(normalized) <= MAX_PROMPT_TEXT_CHARS:
        return normalized
    return f"{normalized[: MAX_PROMPT_TEXT_CHARS - 1].rstrip()}..."

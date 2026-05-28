from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
import json
import re
from typing import Any, Protocol

from sqlalchemy.orm import Session

from app.core import Settings, get_settings
from app.schemas.answer import (
    AnswerCitationRead,
    AnswerEvidenceContextRead,
    CitationValidationIssueRead,
    CitationValidationRead,
    GeneratedAnswerCandidate,
    ResearchAnswerResponse,
    ResearchQueryRequest,
)
from app.schemas.retrieval import (
    EvidencePackRead,
    EvidenceSpanRead,
    MetricComparisonRead,
    RetrievedChunkRead,
    RetrievedFinancialFactRead,
    RetrievalResponse,
)
from app.services.answer_context import build_answer_evidence_context
from app.services.retrieval import RetrievalService

CITATION_MARKER_RE = re.compile(r"\[([a-z_]+:[^\]]+)\]")
INVESTMENT_ADVICE_RE = re.compile(
    r"\b(?:buy|sell|hold|price target|target price|rating|outperform|underperform|"
    r"overweight|underweight|strong buy|investment recommendation)\b",
    re.IGNORECASE,
)
FACTUAL_TRIGGER_RE = re.compile(
    r"(\$|\b\d[\d,.]*(?:%|\s*(?:billion|million|thousand|usd|bps|basis points))\b|"
    r"\b20\d{2}\b|\b(?:10-k|10-q|8-k|sec|filing|filed|fiscal|quarter|annual|"
    r"revenue|sales|income|margin|cash flow|profit|capex|free cash flow|"
    r"increased|decreased|higher|lower|growth|decline|risk|risks|adversely|"
    r"regulatory|competition|macroeconomic|because|due to|driven by|primarily|"
    r"caused|attributable|resulted from)\b)",
    re.IGNORECASE,
)
FINANCIAL_NUMBER_RE = re.compile(
    r"(\$|\b\d[\d,.]*(?:%|\s*(?:billion|million|thousand|usd|bps|basis points))\b)",
    re.IGNORECASE,
)
UNCITED_ALLOWED_PREFIXES = (
    "overall",
    "in summary",
    "taken together",
    "however",
    "the available evidence",
    "available evidence",
    "the retrieved evidence",
    "based on the retrieved evidence",
)
LIMITATION_TERMS = (
    "limited",
    "limitation",
    "insufficient",
    "not enough",
    "not available",
    "not provided",
    "does not provide",
    "could not determine",
)
EVIDENCE_ROLE_ORDER = (
    "primary_financial_statement",
    "mda_explanation",
    "segment_or_product_breakdown",
    "risk_factor",
    "annual_context",
)
FINANCIAL_METRIC_TERMS = {
    "cash_and_cash_equivalents": (
        "cash and cash equivalents",
        "cash equivalents",
        "cash position",
        "cash balance",
        "cash balances",
    ),
    "revenue": ("revenue", "sales", "net sales"),
    "net_sales": ("revenue", "sales", "net sales"),
    "gross_profit": ("gross profit", "profit"),
    "gross_margin": ("gross margin", "margin"),
    "operating_income": ("operating income", "income"),
    "net_income": ("net income", "earnings", "income"),
    "earnings_per_share": ("earnings per share", "eps"),
    "operating_cash_flow": ("operating cash flow", "cash flow"),
    "capital_expenditures": ("capital expenditures", "capex"),
    "free_cash_flow": ("free cash flow", "fcf", "cash flow"),
}


class AnswerGenerationError(RuntimeError):
    """Raised when answer generation cannot produce a candidate."""


class AnswerCandidateProvider(Protocol):
    def generate_candidate(self, prompt: str) -> GeneratedAnswerCandidate:
        """Generate an answer candidate from a complete grounded prompt."""


@dataclass(frozen=True)
class PromptEvidenceContext:
    prompt: str
    prompt_evidence_ids: list[str]
    evidence_by_id: dict[str, Any]


class OpenAIAnswerCandidateProvider:
    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()

    def generate_candidate(self, prompt: str) -> GeneratedAnswerCandidate:
        api_key = self._settings.openai_api_key
        if api_key is None or not api_key.get_secret_value().strip():
            raise AnswerGenerationError("OPENAI_API_KEY must be configured for answer generation.")

        try:
            from openai import OpenAI
        except ImportError as exc:
            raise AnswerGenerationError(
                "The openai package must be installed for answer generation."
            ) from exc

        client = OpenAI(
            api_key=api_key.get_secret_value(),
            timeout=self._settings.answer_llm_timeout_seconds,
        )
        response = client.chat.completions.create(
            model=self._settings.answer_llm_model,
            temperature=0,
            response_format={"type": "json_object"},
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are a citation-grounded equity research assistant. "
                        "You must answer only from the provided SEC evidence."
                    ),
                },
                {"role": "user", "content": prompt},
            ],
        )
        content = response.choices[0].message.content or "{}"
        try:
            payload = json.loads(content)
        except json.JSONDecodeError as exc:
            raise AnswerGenerationError("Answer LLM returned invalid JSON.") from exc
        try:
            return GeneratedAnswerCandidate.model_validate(payload)
        except Exception as exc:
            raise AnswerGenerationError("Answer LLM returned an invalid answer schema.") from exc


class AnswerService:
    def __init__(
        self,
        db: Session,
        *,
        settings: Settings | None = None,
        retriever: RetrievalService | None = None,
        provider: AnswerCandidateProvider | None = None,
    ) -> None:
        self._db = db
        self._settings = settings or get_settings()
        self._retriever = retriever
        self._provider = provider

    def answer(self, request: ResearchQueryRequest) -> ResearchAnswerResponse:
        retrieval_response = RetrievalResponse.model_validate(
            self._get_retriever().retrieve(request)
        )
        context = build_answer_evidence_context(request, retrieval_response)
        prompt_context = build_prompt_evidence_context(context)
        if not prompt_context.prompt_evidence_ids:
            validation = failed_validation(
                context,
                prompt_context,
                [
                    CitationValidationIssueRead(
                        code="no_prompt_evidence",
                        message="No selected evidence was available for answer generation.",
                    )
                ],
            )
            return insufficient_evidence_response(context, prompt_context, validation)

        validation: CitationValidationRead | None = None
        candidate: GeneratedAnswerCandidate | None = None
        for attempt in range(2):
            prompt = prompt_context.prompt
            if validation is not None:
                prompt = add_retry_instructions(prompt, validation, prompt_context)
            candidate = self._get_provider().generate_candidate(prompt)
            validation = validate_generated_answer(
                candidate,
                context,
                prompt_context,
            )
            if validation.valid:
                return validated_answer_response(context, prompt_context, candidate, validation)

            repaired_candidate = repair_missing_financial_citations(
                candidate,
                validation,
                prompt_context,
            )
            if repaired_candidate is not None:
                repaired_validation = validate_generated_answer(
                    repaired_candidate,
                    context,
                    prompt_context,
                )
                if repaired_validation.valid:
                    return validated_answer_response(
                        context,
                        prompt_context,
                        repaired_candidate,
                        repaired_validation,
                    )
                candidate = repaired_candidate
                validation = repaired_validation

        assert validation is not None
        return insufficient_evidence_response(
            context,
            prompt_context,
            validation,
            candidate=candidate,
        )

    def _get_retriever(self) -> RetrievalService:
        if self._retriever is None:
            self._retriever = RetrievalService(self._db)
        return self._retriever

    def _get_provider(self) -> AnswerCandidateProvider:
        if self._provider is None:
            self._provider = OpenAIAnswerCandidateProvider(self._settings)
        return self._provider


def build_prompt_evidence_context(context: AnswerEvidenceContextRead) -> PromptEvidenceContext:
    evidence_by_id = build_evidence_lookup(context)
    prompt_ids: list[str] = []
    evidence_lines: list[str] = []

    for comparison in context.final_evidence_pack.metric_comparisons:
        append_prompt_evidence(
            prompt_ids,
            evidence_lines,
            comparison.evidence_id,
            format_metric_comparison_for_prompt(comparison),
        )

    span_roles = evidence_span_roles(context.final_evidence_pack)
    chunk_roles = evidence_chunk_roles(context.final_evidence_pack)
    for role in EVIDENCE_ROLE_ORDER:
        spans = span_roles[role]
        if spans:
            for span in spans:
                append_prompt_evidence(
                    prompt_ids,
                    evidence_lines,
                    span.evidence_id,
                    format_span_for_prompt(span),
                )
            continue
        for chunk in chunk_roles[role]:
            append_prompt_evidence(
                prompt_ids,
                evidence_lines,
                chunk.evidence_id,
                format_chunk_for_prompt(chunk),
            )

    comparison_fact_ids = {
        fact_id
        for comparison in context.final_evidence_pack.metric_comparisons
        for fact_id in (comparison.current_fact_id, comparison.prior_fact_id)
    }
    if comparison_fact_ids:
        facts = [
            fact for fact in context.retrieved_facts if fact.fact_id in comparison_fact_ids
        ]
    elif context.retrieval_plan.needs_financial_facts:
        facts = context.retrieved_facts
    else:
        facts = []
    for fact in facts:
        append_prompt_evidence(
            prompt_ids,
            evidence_lines,
            fact.evidence_id,
            format_fact_for_prompt(fact),
        )

    prompt = build_answer_prompt(context, evidence_lines)
    return PromptEvidenceContext(
        prompt=prompt,
        prompt_evidence_ids=prompt_ids,
        evidence_by_id=evidence_by_id,
    )


def append_prompt_evidence(
    prompt_ids: list[str],
    evidence_lines: list[str],
    evidence_id: str,
    line: str,
) -> None:
    if evidence_id in prompt_ids:
        return
    prompt_ids.append(evidence_id)
    evidence_lines.append(line)


def build_answer_prompt(
    context: AnswerEvidenceContextRead,
    evidence_lines: list[str],
) -> str:
    return "\n".join(
        [
            "Answer the user question using only the evidence below.",
            "Return JSON only with keys: answer, limitations.",
            "The answer must be a string. limitations must be an array of strings.",
            "Use citation markers exactly as shown, for example [financial_fact:501].",
            "Every factual sentence must include citation markers.",
            (
                "If a sentence contains a dollar amount, percentage, growth rate, "
                "sales/revenue increase amount, or other financial number, cite a "
                "[financial_fact:...] or [metric_comparison:...] marker in that same sentence."
            ),
            (
                "Driver or explanation sentences should cite text span/chunk evidence; "
                "a sentence may include both financial and text citations."
            ),
            "Do not provide investment advice, ratings, price targets, or buy/sell/hold recommendations.",
            "If the evidence is insufficient, say so in the answer and limitations.",
            "",
            f"Ticker: {context.ticker}",
            f"Question: {context.question}",
            "",
            "Evidence:",
            *(evidence_lines or ["No selected evidence."]),
        ]
    )


def add_retry_instructions(
    prompt: str,
    validation: CitationValidationRead,
    prompt_context: PromptEvidenceContext,
) -> str:
    errors = "\n".join(
        f"- {issue.code}: {issue.message}" for issue in validation.errors[:8]
    )
    guidance: list[str] = []
    if any(issue.code == "missing_financial_fact_citation" for issue in validation.errors):
        financial_ids = financial_prompt_evidence_ids(prompt_context)
        if financial_ids:
            markers = ", ".join(f"[{evidence_id}]" for evidence_id in financial_ids[:8])
            guidance.append(
                "For missing_financial_fact_citation, add one of these exact financial "
                f"evidence ids in the same sentence as the financial number: {markers}."
            )
    guidance_text = f"\n{chr(10).join(guidance)}" if guidance else ""
    return (
        f"{prompt}\n\n"
        "Previous answer failed validation. Fix these issues and return JSON only:\n"
        f"{errors}"
        f"{guidance_text}"
    )


def validate_generated_answer(
    candidate: GeneratedAnswerCandidate,
    context: AnswerEvidenceContextRead,
    prompt_context: PromptEvidenceContext,
) -> CitationValidationRead:
    errors: list[CitationValidationIssueRead] = []
    cited_ids = extract_citation_ids(candidate.answer)
    allowed_id_set = set(context.allowed_evidence_ids)
    prompt_id_set = set(prompt_context.prompt_evidence_ids)

    if INVESTMENT_ADVICE_RE.search(candidate.answer):
        errors.append(
            CitationValidationIssueRead(
                code="investment_advice",
                message="The answer appears to contain investment advice or rating language.",
            )
        )

    for evidence_id in cited_ids:
        if evidence_id not in allowed_id_set:
            errors.append(
                CitationValidationIssueRead(
                    code="unknown_citation",
                    message="Citation id was not part of the retrieved answer evidence.",
                    evidence_id=evidence_id,
                )
            )
        elif evidence_id not in prompt_id_set:
            errors.append(
                CitationValidationIssueRead(
                    code="out_of_prompt_citation",
                    message="Citation id was allowed but was not shown to the answer model.",
                    evidence_id=evidence_id,
                )
            )

    if not cited_ids:
        errors.append(
            CitationValidationIssueRead(
                code="missing_citations",
                message="A normal answer must include at least one evidence citation.",
            )
        )

    has_financial_evidence = any(
        evidence_id.startswith(("financial_fact:", "metric_comparison:"))
        for evidence_id in prompt_context.prompt_evidence_ids
    )
    for sentence in split_answer_sentences(candidate.answer):
        sentence_citations = extract_citation_ids(sentence)
        if not sentence_citations and sentence_requires_citation(sentence):
            errors.append(
                CitationValidationIssueRead(
                    code="uncited_factual_claim",
                    message="A factual claim sentence is missing a citation.",
                    sentence=sentence,
                )
            )
        if (
            has_financial_evidence
            and has_financial_number(sentence)
            and sentence_citations
            and not any(
                citation.startswith(("financial_fact:", "metric_comparison:"))
                for citation in sentence_citations
            )
        ):
            errors.append(
                CitationValidationIssueRead(
                    code="missing_financial_fact_citation",
                    message=(
                        "A financial-number sentence must cite a financial fact "
                        "or metric comparison when available."
                    ),
                    sentence=sentence,
                )
            )

    return CitationValidationRead(
        status="failed" if errors else "passed",
        cited_evidence_ids=cited_ids,
        allowed_evidence_ids=context.allowed_evidence_ids,
        prompt_evidence_ids=prompt_context.prompt_evidence_ids,
        errors=errors,
    )


def validated_answer_response(
    context: AnswerEvidenceContextRead,
    prompt_context: PromptEvidenceContext,
    candidate: GeneratedAnswerCandidate,
    validation: CitationValidationRead,
) -> ResearchAnswerResponse:
    cited_ids = validation.cited_evidence_ids
    return ResearchAnswerResponse(
        answer=candidate.answer,
        citations=build_answer_citations(cited_ids, prompt_context.evidence_by_id),
        retrieved_evidence_ids=context.allowed_evidence_ids,
        prompt_evidence_ids=prompt_context.prompt_evidence_ids,
        validation_status="passed",
        validation=validation,
        limitations=candidate.limitations,
        source_coverage_summary=context.source_coverage_summary,
        retrieval_plan=context.retrieval_plan,
        final_evidence_pack=context.final_evidence_pack,
    )


def repair_missing_financial_citations(
    candidate: GeneratedAnswerCandidate,
    validation: CitationValidationRead,
    prompt_context: PromptEvidenceContext,
) -> GeneratedAnswerCandidate | None:
    missing_financial_issues = [
        issue
        for issue in validation.errors
        if issue.code == "missing_financial_fact_citation" and issue.sentence
    ]
    if not missing_financial_issues:
        return None
    if any(issue.code != "missing_financial_fact_citation" for issue in validation.errors):
        return None

    issue_sentences = {issue.sentence for issue in missing_financial_issues if issue.sentence}
    prompt_id_set = set(prompt_context.prompt_evidence_ids)
    sentences = split_answer_sentences(candidate.answer)
    repaired_sentences: list[str] = []
    changed = False

    for sentence in sentences:
        if sentence not in issue_sentences:
            repaired_sentences.append(sentence)
            continue

        sentence_citations = extract_citation_ids(sentence)
        if not sentence_citations or any(
            evidence_id not in prompt_id_set for evidence_id in sentence_citations
        ):
            return None
        financial_evidence_id = choose_financial_evidence_id(sentence, prompt_context)
        if financial_evidence_id is None:
            return None
        repaired_sentences.append(append_citation_to_sentence(sentence, financial_evidence_id))
        changed = True

    if not changed:
        return None
    return GeneratedAnswerCandidate(
        answer=" ".join(repaired_sentences),
        limitations=candidate.limitations,
    )


def choose_financial_evidence_id(
    sentence: str,
    prompt_context: PromptEvidenceContext,
) -> str | None:
    candidates = [
        (evidence_id, prompt_context.evidence_by_id.get(evidence_id))
        for evidence_id in financial_prompt_evidence_ids(prompt_context)
    ]
    candidates = [
        (evidence_id, evidence)
        for evidence_id, evidence in candidates
        if isinstance(evidence, MetricComparisonRead | RetrievedFinancialFactRead)
    ]
    if not candidates:
        return None

    normalized_sentence = sentence.lower()

    def score(candidate: tuple[str, MetricComparisonRead | RetrievedFinancialFactRead]) -> int:
        evidence_id, evidence = candidate
        metric_terms = financial_metric_terms(evidence)
        term_score = sum(1 for term in metric_terms if term and term in normalized_sentence)
        type_bonus = 10 if evidence_id.startswith("metric_comparison:") else 0
        return term_score * 100 + type_bonus

    return max(candidates, key=score)[0]


def financial_metric_terms(
    evidence: MetricComparisonRead | RetrievedFinancialFactRead,
) -> tuple[str, ...]:
    key = evidence.canonical_metric_key.lower()
    terms = {key.replace("_", " "), *FINANCIAL_METRIC_TERMS.get(key, ())}
    if isinstance(evidence, RetrievedFinancialFactRead):
        terms.add(evidence.label.lower())
    return tuple(terms)


def financial_prompt_evidence_ids(prompt_context: PromptEvidenceContext) -> list[str]:
    metric_comparison_ids = [
        evidence_id
        for evidence_id in prompt_context.prompt_evidence_ids
        if evidence_id.startswith("metric_comparison:")
    ]
    financial_fact_ids = [
        evidence_id
        for evidence_id in prompt_context.prompt_evidence_ids
        if evidence_id.startswith("financial_fact:")
    ]
    return [*metric_comparison_ids, *financial_fact_ids]


def append_citation_to_sentence(sentence: str, evidence_id: str) -> str:
    stripped = sentence.rstrip()
    if stripped.endswith((".", "!", "?")):
        return f"{stripped[:-1]} [{evidence_id}]{stripped[-1]}"
    return f"{stripped} [{evidence_id}]"


def failed_validation(
    context: AnswerEvidenceContextRead,
    prompt_context: PromptEvidenceContext,
    errors: list[CitationValidationIssueRead],
) -> CitationValidationRead:
    return CitationValidationRead(
        status="failed",
        cited_evidence_ids=[],
        allowed_evidence_ids=context.allowed_evidence_ids,
        prompt_evidence_ids=prompt_context.prompt_evidence_ids,
        errors=errors,
    )


def insufficient_evidence_response(
    context: AnswerEvidenceContextRead,
    prompt_context: PromptEvidenceContext,
    validation: CitationValidationRead,
    *,
    candidate: GeneratedAnswerCandidate | None = None,
) -> ResearchAnswerResponse:
    limitations = list(candidate.limitations if candidate is not None else [])
    if not limitations:
        limitations = ["The retrieved SEC evidence was insufficient for a validated answer."]
    return ResearchAnswerResponse(
        answer="I do not have enough validated SEC evidence to answer this question reliably.",
        citations=[],
        retrieved_evidence_ids=context.allowed_evidence_ids,
        prompt_evidence_ids=prompt_context.prompt_evidence_ids,
        validation_status="insufficient_evidence",
        validation=validation,
        limitations=limitations,
        source_coverage_summary=context.source_coverage_summary,
        retrieval_plan=context.retrieval_plan,
        final_evidence_pack=context.final_evidence_pack,
    )


def extract_citation_ids(text_value: str) -> list[str]:
    return list(dict.fromkeys(match.group(1) for match in CITATION_MARKER_RE.finditer(text_value)))


def split_answer_sentences(answer: str) -> list[str]:
    normalized = " ".join(answer.split())
    sentences = [
        " ".join(sentence.split())
        for sentence in re.split(r"(?<=[.!?])\s+(?=[A-Z])", normalized)
    ]
    return [sentence for sentence in sentences if sentence]


def sentence_requires_citation(sentence: str) -> bool:
    normalized = sentence.strip().lower()
    if not normalized:
        return False
    markerless = CITATION_MARKER_RE.sub("", normalized).strip()
    if not markerless:
        return False
    if markerless.startswith(UNCITED_ALLOWED_PREFIXES) and not has_financial_number(markerless):
        return False
    if any(term in markerless for term in LIMITATION_TERMS):
        return False
    return FACTUAL_TRIGGER_RE.search(markerless) is not None


def has_financial_number(sentence: str) -> bool:
    return FINANCIAL_NUMBER_RE.search(CITATION_MARKER_RE.sub("", sentence)) is not None


def build_evidence_lookup(context: AnswerEvidenceContextRead) -> dict[str, Any]:
    pack = context.final_evidence_pack
    evidence: dict[str, Any] = {}
    for comparison in pack.metric_comparisons:
        evidence[comparison.evidence_id] = comparison
    for chunk in all_pack_chunks(pack):
        evidence[chunk.evidence_id] = chunk
    for span in all_pack_spans(pack):
        evidence[span.evidence_id] = span
    for fact in context.retrieved_facts:
        evidence[fact.evidence_id] = fact
    return evidence


def build_answer_citations(
    cited_ids: list[str],
    evidence_by_id: dict[str, Any],
) -> list[AnswerCitationRead]:
    citations: list[AnswerCitationRead] = []
    for evidence_id in cited_ids:
        evidence = evidence_by_id.get(evidence_id)
        if evidence is None:
            continue
        citations.append(build_answer_citation(evidence))
    return citations


def build_answer_citation(evidence: Any) -> AnswerCitationRead:
    if isinstance(evidence, EvidenceSpanRead):
        return AnswerCitationRead(
            evidence_id=evidence.evidence_id,
            evidence_type=evidence.type,
            source_label=evidence.support_kind,
            text=evidence.text,
            sec_url=evidence.sec_url,
            form_type=evidence.form_type,
            filing_date=evidence.filing_date,
            section=evidence.section_label,
            pages=format_pages(evidence.start_page, evidence.end_page),
            source_ids={
                "chunk_id": evidence.chunk_id,
                "source_chunk_evidence_id": evidence.source_chunk_evidence_id,
                "accession_number": evidence.accession_number,
            },
        )
    if isinstance(evidence, RetrievedChunkRead):
        return AnswerCitationRead(
            evidence_id=evidence.evidence_id,
            evidence_type=evidence.type,
            source_label=evidence.section_label,
            text=evidence.snippet,
            sec_url=evidence.sec_url,
            form_type=evidence.form_type,
            filing_date=evidence.filing_date,
            section=evidence.section_label,
            pages=format_pages(evidence.start_page, evidence.end_page),
            source_ids={
                "chunk_id": evidence.chunk_id,
                "filing_id": evidence.filing_id,
                "section_id": evidence.section_id,
                "accession_number": evidence.accession_number,
            },
        )
    if isinstance(evidence, RetrievedFinancialFactRead):
        return AnswerCitationRead(
            evidence_id=evidence.evidence_id,
            evidence_type=evidence.type,
            source_label=evidence.label,
            text=format_fact_value(evidence),
            sec_url=evidence.source_filing_url,
            form_type=evidence.form_type,
            filing_date=evidence.filed_date,
            section=None,
            pages=None,
            source_ids={
                "fact_id": evidence.fact_id,
                "canonical_metric_key": evidence.canonical_metric_key,
                "source_filing_id": evidence.source_filing_id,
                "source_accession_number": evidence.source_accession_number,
                "source_fact_id": evidence.source_fact_id,
            },
        )
    if isinstance(evidence, MetricComparisonRead):
        return AnswerCitationRead(
            evidence_id=evidence.evidence_id,
            evidence_type=evidence.type,
            source_label=f"{evidence.canonical_metric_key} {evidence.basis}",
            text=format_metric_comparison_text(evidence),
            sec_url=evidence.current_source_filing_url or evidence.prior_source_filing_url,
            form_type=None,
            filing_date=None,
            section=None,
            pages=None,
            source_ids={
                "canonical_metric_key": evidence.canonical_metric_key,
                "basis": evidence.basis,
                "current_fact_id": evidence.current_fact_id,
                "prior_fact_id": evidence.prior_fact_id,
                "current_source_filing_url": evidence.current_source_filing_url,
                "prior_source_filing_url": evidence.prior_source_filing_url,
            },
        )
    raise TypeError(f"Unsupported citation evidence type: {type(evidence)!r}")


def evidence_span_roles(pack: EvidencePackRead) -> dict[str, list[EvidenceSpanRead]]:
    return {
        "primary_financial_statement": pack.primary_financial_statement_spans,
        "mda_explanation": pack.mda_explanation_spans,
        "segment_or_product_breakdown": pack.segment_or_product_breakdown_spans,
        "risk_factor": pack.risk_factor_spans,
        "annual_context": pack.annual_context_spans,
    }


def evidence_chunk_roles(pack: EvidencePackRead) -> dict[str, list[RetrievedChunkRead]]:
    return {
        "primary_financial_statement": pack.primary_financial_statement_chunks,
        "mda_explanation": pack.mda_explanation_chunks,
        "segment_or_product_breakdown": pack.segment_or_product_breakdown_chunks,
        "risk_factor": pack.risk_factor_chunks,
        "annual_context": pack.annual_context_chunks,
    }


def all_pack_chunks(pack: EvidencePackRead) -> list[RetrievedChunkRead]:
    return [
        *pack.primary_financial_statement_chunks,
        *pack.mda_explanation_chunks,
        *pack.segment_or_product_breakdown_chunks,
        *pack.risk_factor_chunks,
        *pack.annual_context_chunks,
    ]


def all_pack_spans(pack: EvidencePackRead) -> list[EvidenceSpanRead]:
    return [
        *pack.primary_financial_statement_spans,
        *pack.mda_explanation_spans,
        *pack.segment_or_product_breakdown_spans,
        *pack.risk_factor_spans,
        *pack.annual_context_spans,
    ]


def format_metric_comparison_for_prompt(comparison: MetricComparisonRead) -> str:
    return (
        f"[{comparison.evidence_id}] Metric comparison: "
        f"{comparison.canonical_metric_key} {comparison.basis}; "
        f"current {comparison.current_period_label or comparison.current_period_end} = "
        f"{format_decimal(comparison.current_value)}; "
        f"prior {comparison.prior_period_label or comparison.prior_period_end} = "
        f"{format_decimal(comparison.prior_value)}; "
        f"growth_rate = {format_decimal(comparison.growth_rate)}."
    )


def format_span_for_prompt(span: EvidenceSpanRead) -> str:
    return (
        f"[{span.evidence_id}] Evidence span from {span.form_type} filed {span.filing_date}, "
        f"{span.section_label}, pages {format_pages(span.start_page, span.end_page) or 'n/a'}: "
        f"{span.text}"
    )


def format_chunk_for_prompt(chunk: RetrievedChunkRead) -> str:
    return (
        f"[{chunk.evidence_id}] Filing chunk from {chunk.form_type} filed {chunk.filing_date}, "
        f"{chunk.section_label}, pages {format_pages(chunk.start_page, chunk.end_page) or 'n/a'}: "
        f"{chunk.snippet}"
    )


def format_fact_for_prompt(fact: RetrievedFinancialFactRead) -> str:
    return (
        f"[{fact.evidence_id}] XBRL fact: {fact.label} ({fact.canonical_metric_key}) "
        f"for {fact.period_label or fact.period_end} = {format_decimal(fact.value)} {fact.unit}; "
        f"filed {fact.filed_date or 'n/a'}; source accession {fact.source_accession_number or 'n/a'}."
    )


def format_metric_comparison_text(comparison: MetricComparisonRead) -> str:
    growth = format_decimal(comparison.growth_rate)
    growth_text = f", growth rate {growth}" if growth is not None else ""
    return (
        f"{comparison.canonical_metric_key}: "
        f"{comparison.current_period_label or comparison.current_period_end} "
        f"{format_decimal(comparison.current_value)} vs "
        f"{comparison.prior_period_label or comparison.prior_period_end} "
        f"{format_decimal(comparison.prior_value)}{growth_text}"
    )


def format_fact_value(fact: RetrievedFinancialFactRead) -> str:
    return (
        f"{fact.label}: {format_decimal(fact.value)} {fact.unit} "
        f"for {fact.period_label or fact.period_end}"
    )


def format_decimal(value: Decimal | None) -> str | None:
    if value is None:
        return None
    return format(value, "f")


def format_pages(start_page: int | None, end_page: int | None) -> str | None:
    if start_page is None and end_page is None:
        return None
    if start_page == end_page or end_page is None:
        return str(start_page)
    if start_page is None:
        return str(end_page)
    return f"{start_page}-{end_page}"

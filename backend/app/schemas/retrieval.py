from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, Field, field_serializer


class RetrievalRequest(BaseModel):
    ticker: str = Field(min_length=1)
    question: str = Field(min_length=1)
    form_type: str | None = None
    date_from: date | None = None
    date_to: date | None = None
    section: str | None = None


class QueryPlanRequest(BaseModel):
    question: str = Field(min_length=1)
    form_type: str | None = None
    section: str | None = None


class RetrievalPlanRead(BaseModel):
    question_type: str
    target_sections: list[str] = Field(default_factory=list)
    metric_keys: list[str] = Field(default_factory=list)
    time_scope: str
    comparison_basis: str = "none"
    comparison_candidates: list[str] = Field(default_factory=list)
    default_comparison_basis: str | None = None
    ambiguities: list[str] = Field(default_factory=list)
    forms: list[str] = Field(default_factory=list)
    preferred_forms: list[str] = Field(default_factory=list)
    dense_queries: list[str] = Field(default_factory=list)
    dense_query_specs: list[dict[str, Any]] = Field(default_factory=list)
    lexical_queries: list[str] = Field(default_factory=list)
    matched_rules: list[str]
    planner_source: str = "llm_validated"
    needs_financial_facts: bool = True
    needs_text_chunks: bool = True
    needs_metric_comparisons: bool = True
    evidence_roles: list[str] = Field(default_factory=list)
    requires_llm_fallback_reason: str | None = None


class RetrievedChunkRead(BaseModel):
    evidence_id: str
    type: Literal["chunk"] = "chunk"
    chunk_id: int
    filing_id: int
    section_id: int
    score: float
    fusion_score: float
    source_ranks: dict[str, int]
    rerank_boosts: dict[str, float]
    snippet: str
    form_type: str
    filing_date: date
    section_label: str
    sec_url: str
    accession_number: str
    start_page: int | None
    end_page: int | None
    has_table: bool


class EvidenceSpanRead(BaseModel):
    evidence_id: str
    type: Literal["evidence_span"] = "evidence_span"
    chunk_id: int
    source_chunk_evidence_id: str
    role: str
    score: float
    support_kind: str
    text: str
    start_char: int | None = None
    end_char: int | None = None
    reasons: list[str] = Field(default_factory=list)
    form_type: str
    filing_date: date
    section_label: str
    sec_url: str
    accession_number: str
    start_page: int | None
    end_page: int | None


class RetrievedFinancialFactRead(BaseModel):
    evidence_id: str
    type: Literal["financial_fact"] = "financial_fact"
    fact_id: int
    score: float
    canonical_metric_key: str
    label: str
    period_start: date | None
    period_end: date
    duration_class: str | None = None
    period_label: str | None = None
    source_fiscal_year: int | None
    fact_fiscal_year: int | None
    fiscal_period: str | None
    form_type: str | None
    filed_date: date | None
    unit: str
    value: Decimal
    source_accession_number: str | None
    source_filing_id: int | None
    source_filing_url: str | None
    source_fact_id: str
    is_computed: bool
    calculation_notes: str | None

    @field_serializer("value")
    def serialize_value(self, value: Decimal) -> str:
        return format(value, "f")


class MetricComparisonRead(BaseModel):
    evidence_id: str
    type: Literal["metric_comparison"] = "metric_comparison"
    basis: str
    canonical_metric_key: str
    current_fact_id: int
    prior_fact_id: int
    current_period_start: date | None
    current_period_end: date
    prior_period_start: date | None
    prior_period_end: date
    current_duration_class: str | None = None
    prior_duration_class: str | None = None
    current_period_label: str | None = None
    prior_period_label: str | None = None
    current_value: Decimal
    prior_value: Decimal
    growth_rate: Decimal | None
    current_source_fiscal_year: int | None
    current_fact_fiscal_year: int | None
    prior_source_fiscal_year: int | None
    prior_fact_fiscal_year: int | None
    current_fiscal_period: str | None
    prior_fiscal_period: str | None
    current_source_filing_url: str | None
    prior_source_filing_url: str | None

    @field_serializer("current_value", "prior_value", "growth_rate")
    def serialize_decimal(self, value: Decimal | None) -> str | None:
        if value is None:
            return None
        return format(value, "f")


class EvidencePackRead(BaseModel):
    metric_comparisons: list[MetricComparisonRead] = Field(default_factory=list)
    primary_financial_statement_chunks: list[RetrievedChunkRead] = Field(default_factory=list)
    mda_explanation_chunks: list[RetrievedChunkRead] = Field(default_factory=list)
    segment_or_product_breakdown_chunks: list[RetrievedChunkRead] = Field(default_factory=list)
    risk_factor_chunks: list[RetrievedChunkRead] = Field(default_factory=list)
    annual_context_chunks: list[RetrievedChunkRead] = Field(default_factory=list)
    primary_financial_statement_spans: list[EvidenceSpanRead] = Field(default_factory=list)
    mda_explanation_spans: list[EvidenceSpanRead] = Field(default_factory=list)
    segment_or_product_breakdown_spans: list[EvidenceSpanRead] = Field(default_factory=list)
    risk_factor_spans: list[EvidenceSpanRead] = Field(default_factory=list)
    annual_context_spans: list[EvidenceSpanRead] = Field(default_factory=list)


class RetrievalResponse(BaseModel):
    retrieval_plan: RetrievalPlanRead
    retrieved_chunks: list[RetrievedChunkRead]
    retrieved_facts: list[RetrievedFinancialFactRead]
    metric_comparisons: list[MetricComparisonRead] = Field(default_factory=list)
    final_evidence_pack: EvidencePackRead = Field(default_factory=EvidencePackRead)
    source_coverage_summary: dict[str, Any]
    retrieval_trace: dict[str, Any]


class RetrievalAnalysisChunkRead(BaseModel):
    evidence_id: str
    chunk_id: int
    filing_id: int
    score: float
    fusion_score: float
    source_ranks: dict[str, int]
    rerank_boosts: dict[str, float]
    form_type: str
    filing_date: date
    section_label: str
    pages: str | None
    snippet: str
    sec_url: str


class RetrievalAnalysisFactRead(BaseModel):
    evidence_id: str
    score: float
    canonical_metric_key: str
    label: str
    period_start: date | None = None
    period_end: date
    duration_class: str | None = None
    period_label: str | None = None
    source_fiscal_year: int | None
    fact_fiscal_year: int | None
    fiscal_period: str | None
    value: Decimal
    unit: str
    source_filing_url: str | None

    @field_serializer("value")
    def serialize_value(self, value: Decimal) -> str:
        return format(value, "f")


class RetrievalAnalysisComparisonRead(BaseModel):
    evidence_id: str
    basis: str
    canonical_metric_key: str
    current_fact_id: int
    prior_fact_id: int
    current_period_end: date
    prior_period_end: date
    current_period_label: str | None = None
    prior_period_label: str | None = None
    current_source_fiscal_year: int | None
    current_fact_fiscal_year: int | None
    prior_source_fiscal_year: int | None
    prior_fact_fiscal_year: int | None
    current_value: Decimal
    prior_value: Decimal
    growth_rate: Decimal | None

    @field_serializer("current_value", "prior_value", "growth_rate")
    def serialize_decimal(self, value: Decimal | None) -> str | None:
        if value is None:
            return None
        return format(value, "f")


class RetrievalAnalysisEvidenceSpanRead(BaseModel):
    evidence_id: str
    chunk_id: int
    source_chunk_evidence_id: str
    role: str
    score: float
    support_kind: str
    text: str
    start_char: int | None = None
    end_char: int | None = None
    reasons: list[str] = Field(default_factory=list)
    form_type: str
    filing_date: date
    section_label: str
    pages: str | None
    sec_url: str


class RetrievalAnalysisEvidencePackRead(BaseModel):
    metric_comparisons: list[RetrievalAnalysisComparisonRead] = Field(default_factory=list)
    primary_financial_statement_chunks: list[RetrievalAnalysisChunkRead] = Field(default_factory=list)
    mda_explanation_chunks: list[RetrievalAnalysisChunkRead] = Field(default_factory=list)
    segment_or_product_breakdown_chunks: list[RetrievalAnalysisChunkRead] = Field(default_factory=list)
    risk_factor_chunks: list[RetrievalAnalysisChunkRead] = Field(default_factory=list)
    annual_context_chunks: list[RetrievalAnalysisChunkRead] = Field(default_factory=list)
    primary_financial_statement_spans: list[RetrievalAnalysisEvidenceSpanRead] = Field(default_factory=list)
    mda_explanation_spans: list[RetrievalAnalysisEvidenceSpanRead] = Field(default_factory=list)
    segment_or_product_breakdown_spans: list[RetrievalAnalysisEvidenceSpanRead] = Field(default_factory=list)
    risk_factor_spans: list[RetrievalAnalysisEvidenceSpanRead] = Field(default_factory=list)
    annual_context_spans: list[RetrievalAnalysisEvidenceSpanRead] = Field(default_factory=list)


class RetrievalAnalysisTraceRead(BaseModel):
    candidate_counts: dict[str, int]
    timing_ms: dict[str, float]
    degraded: list[dict[str, str]]
    retrieval_config: dict[str, Any]
    top_score_breakdown: list[dict[str, Any]]


class RetrievalAnalysisResponse(BaseModel):
    retrieval_plan: RetrievalPlanRead
    source_coverage_summary: dict[str, Any]
    final_evidence_pack: RetrievalAnalysisEvidencePackRead = Field(
        default_factory=RetrievalAnalysisEvidencePackRead
    )
    top_chunks: list[RetrievalAnalysisChunkRead]
    top_facts: list[RetrievalAnalysisFactRead]
    metric_comparisons: list[RetrievalAnalysisComparisonRead] = Field(default_factory=list)
    analysis_trace: RetrievalAnalysisTraceRead

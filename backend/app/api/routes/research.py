from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.db import get_db_session
from app.schemas import (
    QueryPlanRequest,
    RetrievalAnalysisResponse,
    RetrievalPlanRead,
    RetrievalRequest,
    RetrievalResponse,
)
from app.services import (
    QueryPlanner,
    RetrievalCompanyNotFoundError,
    RetrievalError,
    RetrievalService,
)

router = APIRouter(prefix="/research", tags=["research"])


@router.post("/plan", response_model=RetrievalPlanRead)
def plan_query(request: QueryPlanRequest) -> RetrievalPlanRead:
    plan = QueryPlanner().plan(
        request.question,
        form_type=request.form_type,
        section=request.section,
    )
    return RetrievalPlanRead.model_validate(plan.to_dict())


@router.post("/retrieve", response_model=RetrievalResponse | RetrievalAnalysisResponse)
def retrieve_evidence(
    request: RetrievalRequest,
    view: Literal["full", "analysis"] = Query(
        default="full",
        description="Use analysis for a compact response focused on retrieval diagnostics.",
    ),
    db: Session = Depends(get_db_session),
) -> RetrievalResponse | RetrievalAnalysisResponse:
    try:
        response = RetrievalResponse.model_validate(RetrievalService(db).retrieve(request))
    except RetrievalCompanyNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RetrievalError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    if view == "analysis":
        return build_analysis_response(response)
    return response


def build_analysis_response(response: RetrievalResponse) -> RetrievalAnalysisResponse:
    trace = response.retrieval_trace
    top_score_breakdown = [
        {
            "evidence_id": chunk.evidence_id,
            "score": chunk.score,
            "fusion_score": chunk.fusion_score,
            "source_ranks": chunk.source_ranks,
            "rerank_boosts": chunk.rerank_boosts,
        }
        for chunk in response.retrieved_chunks
    ]

    return RetrievalAnalysisResponse(
        retrieval_plan=response.retrieval_plan,
        source_coverage_summary=response.source_coverage_summary,
        final_evidence_pack={
            "metric_comparisons": [
                build_analysis_comparison(comparison)
                for comparison in response.final_evidence_pack.metric_comparisons
            ],
            "primary_financial_statement_chunks": [
                build_analysis_chunk(chunk)
                for chunk in response.final_evidence_pack.primary_financial_statement_chunks
            ],
            "mda_explanation_chunks": [
                build_analysis_chunk(chunk)
                for chunk in response.final_evidence_pack.mda_explanation_chunks
            ],
            "segment_or_product_breakdown_chunks": [
                build_analysis_chunk(chunk)
                for chunk in response.final_evidence_pack.segment_or_product_breakdown_chunks
            ],
            "risk_factor_chunks": [
                build_analysis_chunk(chunk)
                for chunk in response.final_evidence_pack.risk_factor_chunks
            ],
            "annual_context_chunks": [
                build_analysis_chunk(chunk)
                for chunk in response.final_evidence_pack.annual_context_chunks
            ],
            "primary_financial_statement_spans": [
                build_analysis_span(span)
                for span in response.final_evidence_pack.primary_financial_statement_spans
            ],
            "mda_explanation_spans": [
                build_analysis_span(span)
                for span in response.final_evidence_pack.mda_explanation_spans
            ],
            "segment_or_product_breakdown_spans": [
                build_analysis_span(span)
                for span in response.final_evidence_pack.segment_or_product_breakdown_spans
            ],
            "risk_factor_spans": [
                build_analysis_span(span)
                for span in response.final_evidence_pack.risk_factor_spans
            ],
            "annual_context_spans": [
                build_analysis_span(span)
                for span in response.final_evidence_pack.annual_context_spans
            ],
        },
        top_chunks=[build_analysis_chunk(chunk) for chunk in response.retrieved_chunks],
        top_facts=[
            {
                "evidence_id": fact.evidence_id,
                "score": fact.score,
                "canonical_metric_key": fact.canonical_metric_key,
                "label": fact.label,
                "period_start": fact.period_start,
                "period_end": fact.period_end,
                "duration_class": fact.duration_class,
                "period_label": fact.period_label,
                "source_fiscal_year": fact.source_fiscal_year,
                "fact_fiscal_year": fact.fact_fiscal_year,
                "fiscal_period": fact.fiscal_period,
                "value": fact.value,
                "unit": fact.unit,
                "source_filing_url": fact.source_filing_url,
            }
            for fact in response.retrieved_facts
        ],
        metric_comparisons=[
            build_analysis_comparison(comparison)
            for comparison in response.metric_comparisons
        ],
        analysis_trace={
            "candidate_counts": trace.get("candidate_counts", {}),
            "timing_ms": trace.get("timing_ms", {}),
            "degraded": trace.get("degraded", []),
            "retrieval_config": trace.get("retrieval_config", {}),
            "top_score_breakdown": top_score_breakdown,
        },
    )


def build_analysis_chunk(chunk) -> dict:
    return {
        "evidence_id": chunk.evidence_id,
        "chunk_id": chunk.chunk_id,
        "filing_id": chunk.filing_id,
        "score": chunk.score,
        "fusion_score": chunk.fusion_score,
        "source_ranks": chunk.source_ranks,
        "rerank_boosts": chunk.rerank_boosts,
        "form_type": chunk.form_type,
        "filing_date": chunk.filing_date,
        "section_label": chunk.section_label,
        "pages": format_pages(chunk.start_page, chunk.end_page),
        "snippet": truncate_snippet(chunk.snippet),
        "sec_url": chunk.sec_url,
    }


def build_analysis_comparison(comparison) -> dict:
    return {
        "evidence_id": comparison.evidence_id,
        "basis": comparison.basis,
        "canonical_metric_key": comparison.canonical_metric_key,
        "current_fact_id": comparison.current_fact_id,
        "prior_fact_id": comparison.prior_fact_id,
        "current_period_end": comparison.current_period_end,
        "prior_period_end": comparison.prior_period_end,
        "current_period_label": comparison.current_period_label,
        "prior_period_label": comparison.prior_period_label,
        "current_source_fiscal_year": comparison.current_source_fiscal_year,
        "current_fact_fiscal_year": comparison.current_fact_fiscal_year,
        "prior_source_fiscal_year": comparison.prior_source_fiscal_year,
        "prior_fact_fiscal_year": comparison.prior_fact_fiscal_year,
        "current_value": comparison.current_value,
        "prior_value": comparison.prior_value,
        "growth_rate": comparison.growth_rate,
    }


def build_analysis_span(span) -> dict:
    return {
        "evidence_id": span.evidence_id,
        "chunk_id": span.chunk_id,
        "source_chunk_evidence_id": span.source_chunk_evidence_id,
        "role": span.role,
        "score": span.score,
        "support_kind": span.support_kind,
        "text": span.text,
        "start_char": span.start_char,
        "end_char": span.end_char,
        "reasons": span.reasons,
        "form_type": span.form_type,
        "filing_date": span.filing_date,
        "section_label": span.section_label,
        "pages": format_pages(span.start_page, span.end_page),
        "sec_url": span.sec_url,
    }


def format_pages(start_page: int | None, end_page: int | None) -> str | None:
    if start_page is None and end_page is None:
        return None
    if start_page == end_page or end_page is None:
        return str(start_page)
    if start_page is None:
        return str(end_page)
    return f"{start_page}-{end_page}"


def truncate_snippet(snippet: str, *, max_chars: int = 260) -> str:
    if len(snippet) <= max_chars:
        return snippet
    return f"{snippet[: max_chars - 1].rstrip()}..."

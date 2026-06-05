from __future__ import annotations

from typing import Any

from app.schemas.answer import ResearchAnswerResponseRead
from app.schemas.research_run import (
    ResearchRunDiagnosticsRead,
    ResearchRunEvidenceRead,
    ResearchRunStepRead,
)
from app.schemas.retrieval import (
    EvidencePackRead,
    EvidenceSpanRead,
    MetricComparisonRead,
    MetricObservationRead,
    RetrievedChunkRead,
    RetrievedFinancialFactRead,
    RetrievalResponse,
)


def build_research_run_steps(
    retrieval_response: RetrievalResponse,
    answer_response: ResearchAnswerResponseRead,
) -> list[ResearchRunStepRead]:
    steps: list[ResearchRunStepRead] = []
    agent_trace = retrieval_response.retrieval_trace.get("agent", {})
    agent_steps = agent_trace.get("steps", [])

    if agent_steps:
        for raw_step in agent_steps:
            action = str(raw_step.get("action") or "agent_step")
            phase = _phase_for_agent_action(action)
            steps.append(
                ResearchRunStepRead(
                    step_id=f"step-{len(steps)}-{action}",
                    step_index=len(steps),
                    phase=phase,
                    name=_name_for_action(action),
                    status="completed",
                    summary=str(
                        raw_step.get("observation_summary")
                        or raw_step.get("thought_summary")
                        or action
                    ),
                    tool_name=None if action in {"analyze_question", "finalize_answer"} else action,
                    tool_input_summary=_dict_or_none(raw_step.get("action_input")),
                    evidence_ids=[str(value) for value in raw_step.get("evidence_ids", [])],
                    degraded_reason=raw_step.get("stop_reason"),
                )
            )
    else:
        steps.append(
            ResearchRunStepRead(
                step_id="step-0-plan-query",
                step_index=0,
                phase="planning",
                name="Plan query",
                status="completed",
                summary=(
                    "Planned retrieval using "
                    f"{retrieval_response.retrieval_plan.question_type} intent."
                ),
                tool_name=None,
                tool_input_summary=retrieval_response.retrieval_plan.model_dump(mode="json"),
                evidence_ids=[],
            )
        )

    steps.append(
        ResearchRunStepRead(
            step_id=f"step-{len(steps)}-answer-generation",
            step_index=len(steps),
            phase="answer_generation",
            name="Generate cited answer",
            status="completed" if answer_response.answer else "failed",
            summary="Generated an answer from the selected evidence pack.",
            tool_name="ResearchAnswerService",
            tool_input_summary={
                "prompt_evidence_count": len(answer_response.prompt_evidence_ids),
                "retrieved_evidence_count": len(answer_response.retrieved_evidence_ids),
            },
            evidence_ids=answer_response.prompt_evidence_ids,
        )
    )
    steps.append(
        ResearchRunStepRead(
            step_id=f"step-{len(steps)}-citation-validation",
            step_index=len(steps),
            phase="validation",
            name="Validate citations",
            status=(
                "completed"
                if answer_response.validation_status == "passed"
                else "degraded"
            ),
            summary=f"Citation validation returned {answer_response.validation_status}.",
            tool_name="CitationValidator",
            tool_input_summary={
                "cited_evidence_ids": answer_response.validation.cited_evidence_ids,
                "error_count": len(answer_response.validation.errors),
            },
            evidence_ids=answer_response.validation.cited_evidence_ids,
            degraded_reason=(
                None
                if answer_response.validation_status == "passed"
                else answer_response.validation_status
            ),
        )
    )
    steps.append(
        ResearchRunStepRead(
            step_id=f"step-{len(steps)}-finalization",
            step_index=len(steps),
            phase="finalization",
            name="Finalize research run",
            status="completed",
            summary="Returned the answer, evidence, validation, and diagnostics.",
            tool_name=None,
            tool_input_summary={"limitations": answer_response.limitations},
            evidence_ids=[citation.evidence_id for citation in answer_response.citations],
        )
    )
    return steps


def build_research_run_evidence(
    retrieval_response: RetrievalResponse,
    answer_response: ResearchAnswerResponseRead,
) -> list[ResearchRunEvidenceRead]:
    del answer_response
    pack = retrieval_response.final_evidence_pack
    evidence: list[ResearchRunEvidenceRead] = []

    for observation in pack.metric_observations:
        evidence.append(_metric_observation_evidence(observation))
    for comparison in pack.metric_comparisons:
        evidence.append(_metric_comparison_evidence(comparison))
    for fact in retrieval_response.retrieved_facts:
        evidence.append(_financial_fact_evidence(fact))
    for role, chunks in _pack_chunk_groups(pack):
        for chunk in chunks:
            evidence.append(_chunk_evidence(chunk, role))
    for role, spans in _pack_span_groups(pack):
        for span in spans:
            evidence.append(_span_evidence(span, role))

    return list({item.evidence_id: item for item in evidence}.values())


def build_research_run_diagnostics(
    retrieval_response: RetrievalResponse,
) -> ResearchRunDiagnosticsRead:
    trace = retrieval_response.retrieval_trace
    return ResearchRunDiagnosticsRead(
        candidate_counts=_int_dict(trace.get("candidate_counts")),
        timing_ms=_float_dict(trace.get("timing_ms")),
        degraded=[
            {"stage": str(item.get("stage")), "reason": str(item.get("reason"))}
            for item in trace.get("degraded", [])
            if isinstance(item, dict)
        ],
        retrieval_config=dict(trace.get("retrieval_config", {})),
        source_coverage_summary=retrieval_response.source_coverage_summary,
        top_score_breakdown=_trace_top_score_breakdown(trace, retrieval_response),
    )


def _phase_for_agent_action(action: str) -> str:
    if action == "analyze_question":
        return "planning"
    if action == "finalize_answer":
        return "finalization"
    return "tool"


def _name_for_action(action: str) -> str:
    names = {
        "analyze_question": "Analyze question",
        "query_xbrl_metrics": "Query XBRL metrics",
        "retrieve_filing_chunks": "Retrieve filing chunks",
        "retrieve_mda": "Retrieve MD&A",
        "retrieve_risk_factors": "Retrieve risk factors",
        "retrieve_segment_discussion": "Retrieve segment discussion",
        "retrieve_prior_filings": "Retrieve prior filings",
        "finalize_answer": "Agent finalization",
    }
    return names.get(action, action.replace("_", " ").title())


def _dict_or_none(value: Any) -> dict[str, Any] | None:
    return value if isinstance(value, dict) else None


def _metric_observation_evidence(observation: MetricObservationRead) -> ResearchRunEvidenceRead:
    return ResearchRunEvidenceRead(
        evidence_id=observation.evidence_id,
        evidence_type=observation.type,
        role="metric_observation",
        title=observation.canonical_metric_key.replace("_", " ").title(),
        text=f"{observation.display_value} for {_period_text(observation.period_start, observation.period_end)}",
        metric_key=observation.canonical_metric_key,
        value=str(observation.value),
        period=_period_text(observation.period_start, observation.period_end),
        form_type=observation.form_type,
        filing_date=_date_str(observation.filed_date),
        section=None,
        sec_url=observation.source_filing_url,
        source_ids={
            "source_filing_id": observation.source_filing_id,
            "source_accession_number": observation.source_accession_number,
            "source_fact_id": observation.source_fact_id,
            "source_fact_evidence_id": observation.source_fact_evidence_id,
        },
    )


def _metric_comparison_evidence(comparison: MetricComparisonRead) -> ResearchRunEvidenceRead:
    title = f"{comparison.canonical_metric_key.replace('_', ' ').title()} comparison"
    text = (
        f"{comparison.current_value} for {_period_text(comparison.current_period_start, comparison.current_period_end)} "
        f"vs {comparison.prior_value} for {_period_text(comparison.prior_period_start, comparison.prior_period_end)}"
    )
    return ResearchRunEvidenceRead(
        evidence_id=comparison.evidence_id,
        evidence_type=comparison.type,
        role="metric_comparison",
        title=title,
        text=text,
        metric_key=comparison.canonical_metric_key,
        value=str(comparison.growth_rate) if comparison.growth_rate is not None else None,
        period=comparison.basis,
        form_type=None,
        filing_date=None,
        section=None,
        sec_url=comparison.current_source_filing_url,
        source_ids={
            "current_fact_id": comparison.current_fact_id,
            "prior_fact_id": comparison.prior_fact_id,
            "prior_source_filing_url": comparison.prior_source_filing_url,
        },
    )


def _financial_fact_evidence(fact: RetrievedFinancialFactRead) -> ResearchRunEvidenceRead:
    return ResearchRunEvidenceRead(
        evidence_id=fact.evidence_id,
        evidence_type=fact.type,
        role="financial_fact",
        title=fact.label,
        text=f"{fact.label}: {fact.value} {fact.unit}",
        metric_key=fact.canonical_metric_key,
        value=str(fact.value),
        period=_period_text(fact.period_start, fact.period_end),
        form_type=fact.form_type,
        filing_date=_date_str(fact.filed_date),
        section=None,
        sec_url=fact.source_filing_url,
        source_ids={
            "fact_id": fact.fact_id,
            "source_fact_id": fact.source_fact_id,
            "source_filing_id": fact.source_filing_id,
        },
    )


def _chunk_evidence(chunk: RetrievedChunkRead, role: str) -> ResearchRunEvidenceRead:
    return ResearchRunEvidenceRead(
        evidence_id=chunk.evidence_id,
        evidence_type=chunk.type,
        role=role,
        title=f"{chunk.form_type} {chunk.section_label}",
        text=chunk.snippet,
        form_type=chunk.form_type,
        filing_date=_date_str(chunk.filing_date),
        section=chunk.section_label,
        sec_url=chunk.sec_url,
        source_ids={
            "chunk_id": chunk.chunk_id,
            "filing_id": chunk.filing_id,
            "section_id": chunk.section_id,
            "accession_number": chunk.accession_number,
        },
    )


def _span_evidence(span: EvidenceSpanRead, role: str) -> ResearchRunEvidenceRead:
    return ResearchRunEvidenceRead(
        evidence_id=span.evidence_id,
        evidence_type=span.type,
        role=role,
        title=f"{span.form_type} {span.section_label}",
        text=span.text,
        form_type=span.form_type,
        filing_date=_date_str(span.filing_date),
        section=span.section_label,
        sec_url=span.sec_url,
        source_ids={
            "chunk_id": span.chunk_id,
            "source_chunk_evidence_id": span.source_chunk_evidence_id,
            "accession_number": span.accession_number,
            "start_char": span.start_char,
            "end_char": span.end_char,
        },
    )


def _pack_chunk_groups(pack: EvidencePackRead) -> list[tuple[str, list[RetrievedChunkRead]]]:
    return [
        ("primary_financial_statement_chunks", pack.primary_financial_statement_chunks),
        ("mda_explanation_chunks", pack.mda_explanation_chunks),
        ("segment_or_product_breakdown_chunks", pack.segment_or_product_breakdown_chunks),
        ("risk_factor_chunks", pack.risk_factor_chunks),
        ("annual_context_chunks", pack.annual_context_chunks),
    ]


def _pack_span_groups(pack: EvidencePackRead) -> list[tuple[str, list[EvidenceSpanRead]]]:
    return [
        ("primary_financial_statement_spans", pack.primary_financial_statement_spans),
        ("mda_explanation_spans", pack.mda_explanation_spans),
        ("segment_or_product_breakdown_spans", pack.segment_or_product_breakdown_spans),
        ("risk_factor_spans", pack.risk_factor_spans),
        ("annual_context_spans", pack.annual_context_spans),
    ]


def _top_score_breakdown(response: RetrievalResponse) -> list[dict[str, Any]]:
    return [
        {
            "evidence_id": chunk.evidence_id,
            "score": chunk.score,
            "fusion_score": chunk.fusion_score,
            "source_ranks": chunk.source_ranks,
            "rerank_boosts": chunk.rerank_boosts,
        }
        for chunk in response.retrieved_chunks
    ]


def _trace_top_score_breakdown(
    trace: dict[str, Any],
    response: RetrievalResponse,
) -> list[dict[str, Any]]:
    value = trace.get("top_score_breakdown")
    if isinstance(value, list):
        return [dict(item) for item in value if isinstance(item, dict)]
    return _top_score_breakdown(response)


def _period_text(start: Any, end: Any) -> str:
    end_text = _date_str(end)
    start_text = _date_str(start)
    if start_text is None:
        return end_text or ""
    return f"{start_text} to {end_text}"


def _date_str(value: Any) -> str | None:
    return value.isoformat() if hasattr(value, "isoformat") else value


def _int_dict(value: Any) -> dict[str, int]:
    if not isinstance(value, dict):
        return {}
    return {str(key): int(raw_value) for key, raw_value in value.items()}


def _float_dict(value: Any) -> dict[str, float]:
    if not isinstance(value, dict):
        return {}
    return {str(key): float(raw_value) for key, raw_value in value.items()}

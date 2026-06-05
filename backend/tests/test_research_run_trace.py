from datetime import date
from decimal import Decimal

from app.schemas import (
    AnswerCitationRead,
    CitationValidationRead,
    EvidencePackRead,
    EvidenceSpanRead,
    MetricComparisonRead,
    MetricObservationComponentRead,
    MetricObservationRead,
    ResearchAnswerResponseRead,
    ResearchRunDiagnosticsRead,
    ResearchRunEvidenceRead,
    ResearchRunRead,
    ResearchRunStepRead,
    RetrievalPlanRead,
    RetrievalResponse,
)
from app.services.research_trace import (
    build_research_run_diagnostics,
    build_research_run_evidence,
    build_research_run_steps,
)


def test_research_run_schema_serializes_minimal_contract() -> None:
    run = ResearchRunRead(
        run_id="run-test",
        status="completed",
        ticker="AAPL",
        question="What drove revenue growth?",
        answer="Revenue grew on cited evidence. [metric_observation:revenue:1]",
        citations=[],
        validation_status="passed",
        validation={"status": "passed", "cited_evidence_ids": []},
        limitations=[],
        plan={"question_type": "mixed"},
        steps=[
            ResearchRunStepRead(
                step_id="step-1",
                step_index=0,
                phase="planning",
                name="Plan query",
                status="completed",
                summary="Planned a mixed evidence request.",
                tool_name=None,
                tool_input_summary=None,
                evidence_ids=[],
                started_at=None,
                finished_at=None,
                duration_ms=None,
                degraded_reason=None,
            )
        ],
        evidence=[
            ResearchRunEvidenceRead(
                evidence_id="metric_observation:revenue:1",
                evidence_type="metric_observation",
                role="metric_observation",
                title="Revenue",
                text="Revenue was $1.00B.",
                metric_key="revenue",
                value="1000000000",
                period="2026-03-31",
                form_type="10-Q",
                filing_date="2026-05-01",
                section=None,
                sec_url="https://www.sec.gov/example",
                source_ids={"fact_id": 1},
            )
        ],
        diagnostics=ResearchRunDiagnosticsRead(
            candidate_counts={"dense": 2},
            timing_ms={"total_ms": 15.0},
            degraded=[],
            retrieval_config={"top_k": 10},
            source_coverage_summary={"chunks": 1},
            top_score_breakdown=[],
        ),
    )

    payload = run.model_dump(mode="json")

    assert payload["contract_version"] == "research_run.v1"
    assert payload["steps"][0]["phase"] == "planning"
    assert payload["evidence"][0]["evidence_id"] == "metric_observation:revenue:1"


def make_plan() -> RetrievalPlanRead:
    return RetrievalPlanRead(
        question_type="mixed",
        target_sections=["Management's Discussion and Analysis"],
        metric_keys=["revenue"],
        time_scope="latest",
        period_kind="quarter",
        target_period="latest",
        duration_class="quarter",
        comparison_basis="latest_quarter_yoy",
        comparison_candidates=["latest_quarter_yoy"],
        default_comparison_basis="latest_quarter_yoy",
        ambiguities=[],
        forms=[],
        allowed_forms=["10-Q"],
        preferred_forms=["10-Q"],
        dense_queries=["revenue drivers"],
        dense_query_specs=[],
        lexical_queries=['"revenue"'],
        lexical_query_specs=[],
        matched_rules=["planner:test"],
        planner_source="llm_validated",
        needs_financial_facts=True,
        needs_text_chunks=True,
        needs_metric_comparisons=True,
        evidence_roles=["metric_comparisons", "mda_explanation_chunks"],
        requires_llm_fallback_reason=None,
    )


def make_retrieval_response() -> RetrievalResponse:
    component = MetricObservationComponentRead(
        evidence_id="metric_observation_component:revenue:service:100",
        fact_id=101,
        canonical_metric_key="service_revenue",
        value=Decimal("600000000"),
        unit="USD",
        display_value="$600M",
        period_start=date(2026, 1, 1),
        period_end=date(2026, 3, 31),
        duration_class="quarter",
        fiscal_period="Q1",
        form_type="10-Q",
        filed_date=date(2026, 5, 1),
        source_filing_id=10,
        source_accession_number="0000000000-26-000001",
        source_filing_url="https://www.sec.gov/filing",
        source_fact_id="fact-101",
    )
    observation = MetricObservationRead(
        evidence_id="metric_observation:revenue:100",
        canonical_metric_key="revenue",
        value=Decimal("1000000000"),
        unit="USD",
        display_value="$1B",
        period_start=date(2026, 1, 1),
        period_end=date(2026, 3, 31),
        duration_class="quarter",
        fiscal_period="Q1",
        form_type="10-Q",
        filed_date=date(2026, 5, 1),
        source_filing_id=10,
        source_accession_number="0000000000-26-000001",
        source_filing_url="https://www.sec.gov/filing",
        source_fact_id="fact-100",
        source_fact_evidence_id="financial_fact:100",
        component_observations=[component],
        confidence=1.0,
    )
    comparison = MetricComparisonRead(
        evidence_id="metric_comparison:revenue:latest_quarter_yoy:100:90",
        basis="latest_quarter_yoy",
        canonical_metric_key="revenue",
        current_fact_id=100,
        prior_fact_id=90,
        current_period_start=date(2026, 1, 1),
        current_period_end=date(2026, 3, 31),
        prior_period_start=date(2025, 1, 1),
        prior_period_end=date(2025, 3, 31),
        current_duration_class="quarter",
        prior_duration_class="quarter",
        current_period_label="Q1 2026 quarter",
        prior_period_label="Q1 2025 quarter",
        current_value=Decimal("1000000000"),
        prior_value=Decimal("800000000"),
        growth_rate=Decimal("0.25"),
        current_source_fiscal_year=2026,
        current_fact_fiscal_year=2026,
        prior_source_fiscal_year=2025,
        prior_fact_fiscal_year=2025,
        current_fiscal_period="Q1",
        prior_fiscal_period="Q1",
        current_source_filing_url="https://www.sec.gov/current",
        prior_source_filing_url="https://www.sec.gov/prior",
    )
    span = EvidenceSpanRead(
        evidence_id="span:10:mda_explanation_chunks:0:50",
        chunk_id=10,
        source_chunk_evidence_id="chunk:10",
        role="mda_explanation_chunks",
        score=0.8,
        support_kind="keyword",
        text="Revenue increased primarily due to stronger services sales.",
        start_char=0,
        end_char=50,
        reasons=["matched revenue driver terms"],
        form_type="10-Q",
        filing_date=date(2026, 5, 1),
        section_label="Management's Discussion and Analysis",
        sec_url="https://www.sec.gov/chunk",
        accession_number="0000000000-26-000001",
        start_page=20,
        end_page=20,
    )
    pack = EvidencePackRead(
        metric_observations=[observation],
        metric_comparisons=[comparison],
        mda_explanation_spans=[span],
    )
    return RetrievalResponse(
        retrieval_plan=make_plan(),
        retrieved_chunks=[],
        retrieved_facts=[],
        metric_comparisons=[comparison],
        final_evidence_pack=pack,
        source_coverage_summary={"mda_explanation_spans": 1},
        retrieval_trace={
            "candidate_counts": {"dense": 3, "lexical": 4, "fused": 5},
            "timing_ms": {"planner_ms": 1.0, "total_ms": 12.0},
            "degraded": [{"stage": "dense", "reason": "embedding_unavailable"}],
            "retrieval_config": {"top_k": 10},
            "top_score_breakdown": [{"evidence_id": "chunk:10", "score": 0.95}],
            "agent": {
                "mode": "react_bounded",
                "stop_reason": "evidence_sufficient",
                "steps": [
                    {
                        "step": 0,
                        "thought_summary": "Analyze the question into evidence needs.",
                        "action": "analyze_question",
                        "action_input": {"question_type": "mixed"},
                        "observation_summary": "Planned mixed retrieval.",
                        "evidence_ids": [],
                        "stop_reason": None,
                    },
                    {
                        "step": 1,
                        "thought_summary": "Metric questions need XBRL facts.",
                        "action": "query_xbrl_metrics",
                        "action_input": {"metric_keys": ["revenue"]},
                        "observation_summary": "Found revenue facts.",
                        "evidence_ids": ["metric_observation:revenue:100"],
                        "stop_reason": None,
                    },
                ],
            },
        },
    )


def make_answer_response() -> ResearchAnswerResponseRead:
    return ResearchAnswerResponseRead(
        answer=(
            "Revenue rose because services sales strengthened. "
            "[metric_observation:revenue:100][span:10:mda_explanation_chunks:0:50]"
        ),
        citations=[
            AnswerCitationRead(
                evidence_id="metric_observation:revenue:100",
                evidence_type="metric_observation",
                source_label="Revenue",
                text="Revenue was $1B.",
                sec_url="https://www.sec.gov/filing",
                form_type="10-Q",
                filing_date="2026-05-01",
                section=None,
                pages=None,
                source_ids={"source_fact_id": "fact-100"},
            )
        ],
        retrieved_evidence_ids=["metric_observation:revenue:100"],
        prompt_evidence_ids=["metric_observation:revenue:100"],
        validation_status="passed",
        validation=CitationValidationRead(
            status="passed",
            cited_evidence_ids=["metric_observation:revenue:100"],
            allowed_evidence_ids=["metric_observation:revenue:100"],
            prompt_evidence_ids=["metric_observation:revenue:100"],
            errors=[],
        ),
        limitations=[],
        source_coverage_summary={"mda_explanation_spans": 1},
        retrieval_plan=make_plan(),
        final_evidence_pack=make_retrieval_response().final_evidence_pack,
    )


def test_trace_builder_converts_agent_steps_to_run_steps() -> None:
    steps = build_research_run_steps(make_retrieval_response(), make_answer_response())

    assert [step.phase for step in steps] == [
        "planning",
        "tool",
        "answer_generation",
        "validation",
        "finalization",
    ]
    assert steps[1].tool_name == "query_xbrl_metrics"
    assert steps[1].evidence_ids == ["metric_observation:revenue:100"]


def test_trace_builder_flattens_evidence_from_pack() -> None:
    evidence = build_research_run_evidence(make_retrieval_response(), make_answer_response())
    evidence_by_id = {item.evidence_id: item for item in evidence}

    assert evidence_by_id["metric_observation:revenue:100"].metric_key == "revenue"
    assert evidence_by_id["metric_observation_component:revenue:service:100"].role == "metric_observation_component"
    assert evidence_by_id["metric_comparison:revenue:latest_quarter_yoy:100:90"].role == "metric_comparison"
    assert evidence_by_id["span:10:mda_explanation_chunks:0:50"].section == "Management's Discussion and Analysis"


def test_trace_builder_copies_diagnostics() -> None:
    diagnostics = build_research_run_diagnostics(make_retrieval_response())

    assert diagnostics.candidate_counts["dense"] == 3
    assert diagnostics.timing_ms["total_ms"] == 12.0
    assert diagnostics.degraded[0]["stage"] == "dense"
    assert diagnostics.retrieval_config["top_k"] == 10
    assert diagnostics.source_coverage_summary["mda_explanation_spans"] == 1
    assert diagnostics.top_score_breakdown == [{"evidence_id": "chunk:10", "score": 0.95}]

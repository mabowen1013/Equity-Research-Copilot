from app.services.query_planner import RetrievalPlan
from app.services.research_agent import (
    ResearchAgentObservation,
    ResearchAgentService,
)


def test_margin_why_question_runs_xbrl_mda_then_segment_followup() -> None:
    agent = ResearchAgentService(max_steps=5)
    state = agent.start(
        question="Why did Apple's margin improve last quarter?",
        plan=make_margin_plan(),
    )

    first = agent.next_action(state)
    assert first.action == "query_xbrl_metrics"
    agent.observe(
        state,
        first,
        observation(
            facts=4,
            metric_observations=1,
            metric_comparisons=1,
        ),
    )

    second = agent.next_action(state)
    assert second.action == "retrieve_mda"
    agent.observe(state, second, observation())

    third = agent.next_action(state)
    assert third.action == "retrieve_segment_discussion"
    agent.observe(
        state,
        third,
        observation(
            segment_or_product_breakdown_chunks=1,
            segment_or_product_breakdown_spans=1,
        ),
    )

    final = agent.next_action(state)
    assert final.action == "finalize_answer"
    assert final.action_input["stop_reason"] == "evidence_sufficient"


def test_risk_question_retrieves_risk_factors_without_xbrl() -> None:
    agent = ResearchAgentService(max_steps=5)
    state = agent.start(
        question="Summarize Apple's latest 10-K risk factors.",
        plan=make_risk_plan(),
    )

    first = agent.next_action(state)
    assert first.action == "retrieve_risk_factors"
    agent.observe(
        state,
        first,
        observation(risk_factor_chunks=1, risk_factor_spans=1),
    )

    final = agent.next_action(state)
    assert final.action == "finalize_answer"
    assert final.action_input["stop_reason"] == "evidence_sufficient"
    assert "query_xbrl_metrics" not in state.actions_taken


def test_pure_metric_question_anchors_xbrl_then_statement_evidence() -> None:
    agent = ResearchAgentService(max_steps=5)
    state = agent.start(
        question="What was Apple's latest quarterly revenue?",
        plan=make_metric_plan(),
    )

    first = agent.next_action(state)
    assert first.action == "query_xbrl_metrics"
    agent.observe(
        state,
        first,
        observation(facts=1, metric_observations=1),
    )

    second = agent.next_action(state)
    assert second.action == "retrieve_filing_chunks"
    agent.observe(
        state,
        second,
        observation(
            primary_financial_statement_chunks=1,
            primary_financial_statement_spans=1,
        ),
    )

    final = agent.next_action(state)
    assert final.action == "finalize_answer"
    assert final.action_input["stop_reason"] == "evidence_sufficient"


def test_text_first_question_retrieves_broad_filing_chunks() -> None:
    agent = ResearchAgentService(max_steps=5)
    state = agent.start(
        question="What does Apple say about its business?",
        plan=make_prose_plan(),
    )

    first = agent.next_action(state)
    assert first.action == "retrieve_filing_chunks"
    agent.observe(state, first, observation(chunks=1))

    final = agent.next_action(state)
    assert final.action == "finalize_answer"
    assert final.action_input["stop_reason"] == "evidence_sufficient"


def test_agent_reports_insufficient_evidence_when_required_roles_stay_empty() -> None:
    agent = ResearchAgentService(max_steps=6)
    state = agent.start(
        question="Why did Apple's margin improve last quarter?",
        plan=make_margin_plan(),
    )

    while True:
        action = agent.next_action(state)
        if action.action == "finalize_answer":
            agent.finish(state, action)
            break
        agent.observe(state, action, observation())

    trace = agent.trace_payload(state)

    assert trace["mode"] == "react_bounded"
    assert trace["stop_reason"] == "insufficient_evidence"
    assert trace["evidence_enough"] is False
    assert trace["steps"][0]["action"] == "analyze_question"
    assert trace["steps"][-1]["action"] == "finalize_answer"
    assert "No matching XBRL metric evidence was found." in trace["limitations"]


def observation(**counts: int) -> ResearchAgentObservation:
    return ResearchAgentObservation(
        observation_summary="test observation",
        evidence_ids=["test:evidence"] if counts else [],
        counts=counts,
    )


def make_margin_plan() -> RetrievalPlan:
    return RetrievalPlan(
        question_type="mixed",
        target_sections=[
            "Financial Statements",
            "Management's Discussion and Analysis",
        ],
        metric_keys=["gross_margin"],
        time_scope="latest",
        period_kind="quarter",
        target_period="latest",
        duration_class="quarter",
        comparison_basis="latest_quarter_yoy",
        comparison_candidates=["latest_quarter_yoy"],
        default_comparison_basis="latest_quarter_yoy",
        ambiguities=[],
        forms=[],
        allowed_forms=["10-Q", "10-K"],
        preferred_forms=["10-Q"],
        dense_queries=["gross margin drivers"],
        lexical_queries=['"gross margin"'],
        matched_rules=["planner:test"],
        needs_financial_facts=True,
        needs_text_chunks=True,
        needs_metric_comparisons=True,
        evidence_roles=[
            "metric_comparisons",
            "primary_financial_statement_chunks",
            "mda_explanation_chunks",
        ],
    )


def make_metric_plan() -> RetrievalPlan:
    return RetrievalPlan(
        question_type="metric",
        target_sections=["Financial Statements"],
        metric_keys=["revenue"],
        time_scope="latest",
        period_kind="quarter",
        target_period="latest",
        duration_class="quarter",
        comparison_basis="none",
        comparison_candidates=[],
        default_comparison_basis=None,
        ambiguities=[],
        forms=[],
        allowed_forms=["10-Q", "10-K"],
        preferred_forms=["10-Q"],
        dense_queries=["latest revenue"],
        lexical_queries=['"net sales"'],
        matched_rules=["planner:test"],
        needs_financial_facts=True,
        needs_text_chunks=True,
        needs_metric_comparisons=False,
        evidence_roles=["primary_financial_statement_chunks"],
    )


def make_risk_plan() -> RetrievalPlan:
    return RetrievalPlan(
        question_type="risk",
        target_sections=["Risk Factors"],
        metric_keys=[],
        time_scope="latest",
        period_kind="fy",
        target_period="latest",
        duration_class="fy",
        comparison_basis="none",
        comparison_candidates=[],
        default_comparison_basis=None,
        ambiguities=[],
        forms=["10-K"],
        allowed_forms=["10-K"],
        preferred_forms=["10-K"],
        dense_queries=["risk factors"],
        lexical_queries=['"risk factors"'],
        matched_rules=["planner:test"],
        needs_financial_facts=False,
        needs_text_chunks=True,
        needs_metric_comparisons=False,
        evidence_roles=["risk_factor_chunks"],
    )


def make_prose_plan() -> RetrievalPlan:
    return RetrievalPlan(
        question_type="prose",
        target_sections=[],
        metric_keys=[],
        time_scope="unspecified",
        period_kind=None,
        target_period=None,
        duration_class=None,
        comparison_basis="none",
        comparison_candidates=[],
        default_comparison_basis=None,
        ambiguities=[],
        forms=[],
        allowed_forms=["10-K", "10-Q"],
        preferred_forms=[],
        dense_queries=["business overview"],
        lexical_queries=["business overview"],
        matched_rules=["planner:test"],
        needs_financial_facts=False,
        needs_text_chunks=True,
        needs_metric_comparisons=False,
        evidence_roles=[],
    )

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.core import Settings, get_settings
from app.services.query_planner import RetrievalPlan


REACT_AGENT_TRACE_VERSION = "v1"
REACT_TOOL_ACTIONS = {
    "query_xbrl_metrics",
    "retrieve_filing_chunks",
    "retrieve_mda",
    "retrieve_risk_factors",
    "retrieve_segment_discussion",
    "retrieve_prior_filings",
}


@dataclass(frozen=True)
class ResearchAgentAction:
    action: str
    thought_summary: str
    action_input: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ResearchAgentObservation:
    observation_summary: str
    evidence_ids: list[str] = field(default_factory=list)
    counts: dict[str, int] = field(default_factory=dict)
    stop_reason: str | None = None


@dataclass
class ResearchAgentState:
    question: str
    plan: RetrievalPlan
    max_steps: int
    steps: list[dict[str, Any]] = field(default_factory=list)
    actions_taken: set[str] = field(default_factory=set)
    tool_step_count: int = 0
    has_metric_evidence: bool = False
    has_metric_comparisons: bool = False
    has_primary_statement: bool = False
    has_mda_explanation: bool = False
    has_segment_discussion: bool = False
    has_risk_factors: bool = False
    has_text_evidence: bool = False
    stop_reason: str | None = None
    limitations: list[str] = field(default_factory=list)


class ResearchAgentService:
    """Bounded ReAct controller for evidence retrieval.

    This service intentionally stores concise thought summaries rather than full
    chain-of-thought. RetrievalService executes the selected actions as tools.
    """

    def __init__(
        self,
        *,
        settings: Settings | None = None,
        max_steps: int | None = None,
    ) -> None:
        self._settings = settings or get_settings()
        configured_max_steps = getattr(self._settings, "research_agent_max_steps", 5)
        self._max_steps = max_steps or configured_max_steps

    def start(self, *, question: str, plan: RetrievalPlan) -> ResearchAgentState:
        state = ResearchAgentState(
            question=question,
            plan=plan,
            max_steps=max(1, self._max_steps),
        )
        state.steps.append(
            {
                "step": 0,
                "thought_summary": "Analyze the question into evidence needs before taking retrieval actions.",
                "action": "analyze_question",
                "action_input": {
                    "question_type": plan.question_type,
                    "metric_keys": plan.metric_keys,
                    "target_sections": plan.target_sections,
                    "time_scope": plan.time_scope,
                    "comparison_basis": plan.comparison_basis,
                    "evidence_roles": plan.evidence_roles,
                },
                "observation_summary": _analysis_summary(plan),
                "evidence_ids": [],
                "stop_reason": None,
            }
        )
        return state

    def next_action(self, state: ResearchAgentState) -> ResearchAgentAction:
        if state.tool_step_count >= state.max_steps:
            return self._finalize_action(
                "max_steps_reached",
                "Stop because the bounded ReAct loop reached its configured step limit.",
                evidence_enough=self.evidence_enough(state),
            )

        if self.evidence_enough(state):
            return self._finalize_action(
                "evidence_sufficient",
                "Stop because the collected evidence satisfies the question's required evidence roles.",
                evidence_enough=True,
            )

        plan = state.plan
        if _needs_risk_factors(plan) and not _taken(state, "retrieve_risk_factors"):
            return ResearchAgentAction(
                action="retrieve_risk_factors",
                thought_summary="Risk questions need Risk Factors evidence before answering.",
                action_input={
                    "target_sections": ["Risk Factors"],
                    "evidence_roles": ["risk_factor_chunks"],
                },
            )

        if plan.needs_financial_facts and not _taken(state, "query_xbrl_metrics"):
            return ResearchAgentAction(
                action="query_xbrl_metrics",
                thought_summary="Metric or change questions should first anchor the answer in XBRL facts.",
                action_input={
                    "metric_keys": plan.metric_keys,
                    "duration_class": plan.duration_class,
                    "comparison_basis": plan.comparison_basis,
                    "comparison_candidates": plan.comparison_candidates,
                },
            )

        if _needs_driver_evidence(state.question, plan) and not _taken(state, "retrieve_mda"):
            return ResearchAgentAction(
                action="retrieve_mda",
                thought_summary="Driver or why questions need MD&A observations after the numeric facts.",
                action_input={
                    "target_sections": ["Management's Discussion and Analysis"],
                    "evidence_roles": ["mda_explanation_chunks"],
                },
            )

        if _needs_segment_followup(state) and not _taken(state, "retrieve_segment_discussion"):
            return ResearchAgentAction(
                action="retrieve_segment_discussion",
                thought_summary="MD&A did not provide enough driver evidence, so retrieve segment or product discussion.",
                action_input={
                    "target_sections": ["Management's Discussion and Analysis"],
                    "evidence_roles": ["segment_or_product_breakdown_chunks"],
                },
            )

        if _needs_primary_statement(plan) and not _taken(state, "retrieve_filing_chunks"):
            return ResearchAgentAction(
                action="retrieve_filing_chunks",
                thought_summary="Retrieve primary filing chunks to connect structured facts to filing text.",
                action_input={
                    "target_sections": _primary_statement_sections(plan),
                    "evidence_roles": ["primary_financial_statement_chunks"],
                },
            )

        if (
            plan.needs_text_chunks
            and not state.has_text_evidence
            and not _taken(state, "retrieve_filing_chunks")
        ):
            return ResearchAgentAction(
                action="retrieve_filing_chunks",
                thought_summary="Retrieve broad filing chunks for a text-first question.",
                action_input={
                    "target_sections": plan.target_sections,
                    "evidence_roles": plan.evidence_roles,
                },
            )

        if _needs_prior_filings(state) and not _taken(state, "retrieve_prior_filings"):
            return ResearchAgentAction(
                action="retrieve_prior_filings",
                thought_summary="Comparison evidence is still thin, so retrieve prior filing context.",
                action_input={
                    "comparison_basis": plan.comparison_basis,
                    "comparison_candidates": plan.comparison_candidates,
                },
            )

        return self._finalize_action(
            "insufficient_evidence",
            "Stop because no remaining retrieval action is likely to fill the missing evidence roles.",
            evidence_enough=False,
        )

    def observe(
        self,
        state: ResearchAgentState,
        action: ResearchAgentAction,
        observation: ResearchAgentObservation,
    ) -> None:
        if action.action not in REACT_TOOL_ACTIONS:
            raise ValueError(f"Unsupported ReAct tool action: {action.action}")

        state.tool_step_count += 1
        state.actions_taken.add(action.action)
        _update_evidence_flags(state, observation.counts)

        state.steps.append(
            {
                "step": len(state.steps),
                "thought_summary": action.thought_summary,
                "action": action.action,
                "action_input": action.action_input,
                "observation_summary": observation.observation_summary,
                "evidence_ids": observation.evidence_ids,
                "stop_reason": observation.stop_reason,
            }
        )

    def finish(
        self,
        state: ResearchAgentState,
        action: ResearchAgentAction,
    ) -> None:
        stop_reason = str(action.action_input.get("stop_reason") or "insufficient_evidence")
        state.stop_reason = stop_reason
        if stop_reason == "insufficient_evidence":
            state.limitations = _missing_evidence_limitations(state)

        state.steps.append(
            {
                "step": len(state.steps),
                "thought_summary": action.thought_summary,
                "action": "finalize_answer",
                "action_input": action.action_input,
                "observation_summary": _final_observation_summary(state),
                "evidence_ids": [],
                "stop_reason": stop_reason,
            }
        )

    def trace_payload(self, state: ResearchAgentState) -> dict[str, Any]:
        return {
            "trace_version": REACT_AGENT_TRACE_VERSION,
            "mode": "react_bounded",
            "max_steps": state.max_steps,
            "tool_step_count": state.tool_step_count,
            "stop_reason": state.stop_reason,
            "evidence_enough": self.evidence_enough(state),
            "limitations": state.limitations,
            "steps": state.steps,
        }

    def evidence_enough(self, state: ResearchAgentState) -> bool:
        plan = state.plan
        metric_ok = (
            not plan.needs_financial_facts
            or state.has_metric_evidence
            or _taken(state, "query_xbrl_metrics") and not plan.metric_keys
        )
        comparison_ok = (
            not _comparison_requested(plan)
            or state.has_metric_comparisons
        )

        if _needs_risk_factors(plan):
            return state.has_risk_factors

        if _needs_driver_evidence(state.question, plan):
            if not metric_ok or not comparison_ok:
                return False
            if state.has_mda_explanation or state.has_segment_discussion:
                return True
            return False

        if _needs_primary_statement(plan) and not state.has_primary_statement:
            if (
                plan.question_type == "metric"
                and state.has_metric_evidence
                and _taken(state, "retrieve_filing_chunks")
            ):
                return metric_ok and comparison_ok
            return False

        if plan.needs_text_chunks and not plan.metric_keys and not state.has_text_evidence:
            return False

        return metric_ok and comparison_ok and (state.has_text_evidence or metric_ok)

    def _finalize_action(
        self,
        stop_reason: str,
        thought_summary: str,
        *,
        evidence_enough: bool,
    ) -> ResearchAgentAction:
        return ResearchAgentAction(
            action="finalize_answer",
            thought_summary=thought_summary,
            action_input={
                "stop_reason": stop_reason,
                "evidence_enough": evidence_enough,
            },
        )


def _analysis_summary(plan: RetrievalPlan) -> str:
    metric_text = ", ".join(plan.metric_keys) if plan.metric_keys else "no XBRL metrics"
    section_text = ", ".join(plan.target_sections) if plan.target_sections else "broad filing text"
    return (
        f"Planned {plan.question_type} retrieval using {metric_text}; "
        f"text evidence target is {section_text}."
    )


def _update_evidence_flags(state: ResearchAgentState, counts: dict[str, int]) -> None:
    if counts.get("facts", 0) or counts.get("metric_observations", 0):
        state.has_metric_evidence = True
    if counts.get("metric_comparisons", 0):
        state.has_metric_comparisons = True
    if counts.get("primary_financial_statement_chunks", 0) or counts.get(
        "primary_financial_statement_spans", 0
    ):
        state.has_primary_statement = True
        state.has_text_evidence = True
    if counts.get("mda_explanation_chunks", 0) or counts.get("mda_explanation_spans", 0):
        state.has_mda_explanation = True
        state.has_text_evidence = True
    if counts.get("segment_or_product_breakdown_chunks", 0) or counts.get(
        "segment_or_product_breakdown_spans", 0
    ):
        state.has_segment_discussion = True
        state.has_text_evidence = True
    if counts.get("risk_factor_chunks", 0) or counts.get("risk_factor_spans", 0):
        state.has_risk_factors = True
        state.has_text_evidence = True
    if counts.get("chunks", 0) or counts.get("evidence_spans", 0):
        state.has_text_evidence = True


def _final_observation_summary(state: ResearchAgentState) -> str:
    if state.stop_reason == "evidence_sufficient":
        return "Evidence is sufficient for cited answer generation."
    if state.stop_reason == "max_steps_reached":
        return "The agent reached its maximum retrieval steps and will answer with available evidence."
    return "Evidence remains incomplete; the answer should include a limitation."


def _missing_evidence_limitations(state: ResearchAgentState) -> list[str]:
    plan = state.plan
    limitations: list[str] = []
    if plan.needs_financial_facts and not state.has_metric_evidence:
        limitations.append("No matching XBRL metric evidence was found.")
    if _comparison_requested(plan) and not state.has_metric_comparisons:
        limitations.append("No comparable prior-period metric evidence was found.")
    if _needs_driver_evidence(state.question, plan) and not (
        state.has_mda_explanation or state.has_segment_discussion
    ):
        limitations.append("No MD&A or segment driver evidence was strong enough.")
    if _needs_risk_factors(plan) and not state.has_risk_factors:
        limitations.append("No Risk Factors evidence was found.")
    if _needs_primary_statement(plan) and not state.has_primary_statement:
        limitations.append("No primary financial statement text evidence was found.")
    return limitations


def _taken(state: ResearchAgentState, action: str) -> bool:
    return action in state.actions_taken


def _needs_risk_factors(plan: RetrievalPlan) -> bool:
    return plan.question_type == "risk" or "Risk Factors" in plan.target_sections


def _needs_driver_evidence(question: str, plan: RetrievalPlan) -> bool:
    normalized = question.lower()
    return (
        "why" in normalized
        or "原因" in question
        or "driver" in normalized
        or "drivers" in normalized
        or "improve" in normalized
        or "improved" in normalized
        or "change" in normalized
        or plan.question_type
        in {
            "mixed",
            "management_discussion",
            "performance_overview",
            "performance_judgment",
            "growth_acceleration",
            "broad_comparison",
        }
        or "Management's Discussion and Analysis" in plan.target_sections
    )


def _needs_segment_followup(state: ResearchAgentState) -> bool:
    plan = state.plan
    if not _needs_driver_evidence(state.question, plan):
        return False
    if state.has_mda_explanation or state.has_segment_discussion:
        return False
    return _taken(state, "retrieve_mda") and bool(plan.metric_keys)


def _needs_primary_statement(plan: RetrievalPlan) -> bool:
    return bool(plan.metric_keys) or "Financial Statements" in plan.target_sections or "Cash Flows" in plan.target_sections


def _primary_statement_sections(plan: RetrievalPlan) -> list[str]:
    sections = [
        section
        for section in plan.target_sections
        if section in {"Financial Statements", "Cash Flows", "Liquidity"}
    ]
    return sections or ["Financial Statements"]


def _needs_prior_filings(state: ResearchAgentState) -> bool:
    return (
        _comparison_requested(state.plan)
        and _taken(state, "query_xbrl_metrics")
        and not state.has_metric_comparisons
    )


def _comparison_requested(plan: RetrievalPlan) -> bool:
    if not plan.needs_metric_comparisons or not plan.metric_keys:
        return False
    if plan.comparison_candidates:
        return True
    return plan.comparison_basis not in {"none", "ambiguous"}

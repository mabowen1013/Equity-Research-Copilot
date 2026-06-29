from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Protocol

from app.core import Settings, get_settings
from app.services.openai_client import get_openai_client
from app.services.query_planner import (
    RetrievalPlan,
    _llm_cache_get,
    _llm_cache_put,
    _parse_llm_json,
)


REACT_AGENT_TRACE_VERSION = "v1"
REACT_TOOL_ACTIONS = {
    "query_xbrl_metrics",
    "retrieve_filing_chunks",
    "retrieve_mda",
    "retrieve_risk_factors",
    "retrieve_segment_discussion",
    "retrieve_prior_filings",
}
FINALIZE_ACTION = "finalize_answer"
ALLOWED_AGENT_ACTIONS = REACT_TOOL_ACTIONS | {FINALIZE_ACTION}


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


class AgentReasoner(Protocol):
    def decide(self, state: "ResearchAgentState") -> "ResearchAgentAction":
        """Choose the next ReAct action given the observations accumulated so far."""


class LLMAgentReasoner:
    """LLM-driven ReAct controller.

    Each step the model reads the question, the plan, and the observations so far,
    then names the single next tool (or finalize_answer). Tool parameters are not
    requested from the model: RetrievalService derives them from the plan and the
    action name, so the model only owns the control decision.
    """

    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()

    def decide(self, state: "ResearchAgentState") -> "ResearchAgentAction":
        api_key = self._settings.openai_api_key
        if api_key is None or not api_key.get_secret_value().strip():
            raise RuntimeError("OPENAI_API_KEY must be configured for LLM ReAct control.")

        payload = _agent_decision_payload(state)
        cache_key = (
            "research_agent_decide",
            self._settings.research_agent_llm_model,
            json.dumps(payload, ensure_ascii=False, sort_keys=True),
        )
        cached = _llm_cache_get(cache_key)
        if cached is not None:
            return _action_from_decision(json.loads(cached))

        try:
            client = get_openai_client(
                api_key.get_secret_value(),
                timeout=self._settings.research_agent_llm_timeout_seconds,
                max_retries=self._settings.research_agent_llm_max_retries,
            )
        except ImportError as exc:
            raise RuntimeError(
                "The openai package must be installed for LLM ReAct control."
            ) from exc

        last_error: Exception | None = None
        for _ in range(2):
            response = client.chat.completions.create(
                model=self._settings.research_agent_llm_model,
                temperature=0,
                response_format={"type": "json_object"},
                messages=[
                    {"role": "system", "content": _agent_controller_system_prompt()},
                    {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
                ],
            )
            content = response.choices[0].message.content or "{}"
            try:
                decision = _parse_agent_decision(content)
            except ValueError as exc:
                last_error = exc
                continue
            _llm_cache_put(cache_key, json.dumps(decision, ensure_ascii=False))
            return _action_from_decision(decision)
        raise ValueError(
            f"LLM ReAct controller returned an invalid decision: {last_error}"
        )


class ResearchAgentService:
    """ReAct controller for evidence retrieval.

    An injectable reasoner (LLM by default) chooses the next action each step from
    the accumulated observations. This service owns the loop bounds, evidence-flag
    bookkeeping, and the trace; RetrievalService executes the selected actions as
    tools. Concise thought summaries are stored rather than full chain-of-thought.
    """

    def __init__(
        self,
        *,
        settings: Settings | None = None,
        max_steps: int | None = None,
        reasoner: AgentReasoner | None = None,
    ) -> None:
        self._settings = settings or get_settings()
        configured_max_steps = getattr(self._settings, "research_agent_max_steps", 5)
        self._max_steps = max_steps or configured_max_steps
        self._reasoner = reasoner or LLMAgentReasoner(self._settings)

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
                "Stop because the ReAct loop reached its configured step limit.",
                evidence_enough=self.evidence_enough(state),
            )

        action = self._reasoner.decide(state)
        if action.action == FINALIZE_ACTION:
            enough = self.evidence_enough(state)
            return self._finalize_action(
                "evidence_sufficient" if enough else "insufficient_evidence",
                action.thought_summary
                or "Stop and answer with the evidence gathered so far.",
                evidence_enough=enough,
            )
        if action.action not in REACT_TOOL_ACTIONS:
            raise ValueError(
                f"LLM ReAct controller chose an unsupported action: {action.action}"
            )
        return action

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
            "mode": "react_llm",
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


def _needs_primary_statement(plan: RetrievalPlan) -> bool:
    return bool(plan.metric_keys) or "Financial Statements" in plan.target_sections or "Cash Flows" in plan.target_sections


def _comparison_requested(plan: RetrievalPlan) -> bool:
    if not plan.needs_metric_comparisons or not plan.metric_keys:
        return False
    if plan.comparison_candidates:
        return True
    return plan.comparison_basis not in {"none", "ambiguous"}


def _agent_decision_payload(state: ResearchAgentState) -> dict[str, Any]:
    plan = state.plan
    return {
        "question": state.question,
        "plan": {
            "question_type": plan.question_type,
            "metric_keys": plan.metric_keys,
            "target_sections": plan.target_sections,
            "comparison_basis": plan.comparison_basis,
            "evidence_roles": plan.evidence_roles,
            "needs_financial_facts": plan.needs_financial_facts,
            "needs_text_chunks": plan.needs_text_chunks,
            "needs_metric_comparisons": plan.needs_metric_comparisons,
        },
        "already_used_actions": sorted(state.actions_taken),
        "evidence_collected": {
            "has_metric_evidence": state.has_metric_evidence,
            "has_metric_comparisons": state.has_metric_comparisons,
            "has_primary_statement": state.has_primary_statement,
            "has_mda_explanation": state.has_mda_explanation,
            "has_segment_discussion": state.has_segment_discussion,
            "has_risk_factors": state.has_risk_factors,
            "has_text_evidence": state.has_text_evidence,
        },
        "steps_taken": state.tool_step_count,
        "max_steps": state.max_steps,
        "history": [
            {
                "action": step["action"],
                "observation": step["observation_summary"],
                "evidence_count": len(step.get("evidence_ids") or []),
            }
            for step in state.steps
        ],
    }


def _parse_agent_decision(content: str) -> dict[str, str]:
    parsed = _parse_llm_json(content)
    action = parsed.get("action")
    if not isinstance(action, str) or action not in ALLOWED_AGENT_ACTIONS:
        raise ValueError(f"Unsupported ReAct action from controller: {action!r}")
    thought = parsed.get("thought")
    return {"action": action, "thought": thought if isinstance(thought, str) else ""}


def _action_from_decision(decision: dict[str, str]) -> ResearchAgentAction:
    return ResearchAgentAction(
        action=decision["action"],
        thought_summary=decision.get("thought") or "Select the next retrieval action.",
        action_input={},
    )


def _agent_controller_system_prompt() -> str:
    return """You are the retrieval controller for Equity Research Copilot, a citation-first SEC research assistant.
You run a ReAct loop: at each step you read the observations gathered so far and choose ONE next action.

Available actions:
- query_xbrl_metrics: fetch structured XBRL financial facts and period-over-period comparisons for the planned metric_keys. Returns numbers, not filing text.
- retrieve_filing_chunks: retrieve primary filing text (financial statements / general filing text) for the planned sections.
- retrieve_mda: retrieve Management's Discussion and Analysis text that explains drivers and context.
- retrieve_risk_factors: retrieve Risk Factors text.
- retrieve_segment_discussion: retrieve segment or product-line breakdown discussion; use after MD&A when driver detail is still thin.
- retrieve_prior_filings: retrieve prior-period filing context; use when a comparison is needed but no comparable prior-period evidence has been found yet.
- finalize_answer: stop retrieving because the gathered evidence is enough to answer, or because no remaining action could fill the gap.

Rules:
- Choose the single action that best fills the biggest remaining evidence gap for the question.
- Do not choose an action listed in already_used_actions; it would return the same evidence. If every useful action is used, choose finalize_answer.
- For numeric or metric questions, anchor in query_xbrl_metrics before retrieving explanatory text.
- For why/driver/change questions, gather MD&A (and segment discussion if MD&A is thin) after the numbers.
- Choose finalize_answer as soon as the evidence is sufficient, or when no listed action can add the missing evidence.
- Retrieval parameters are derived from the plan automatically; you only choose the action name.

Return one JSON object with exactly these fields: {"thought": "<one concise sentence>", "action": "<one action name>"}."""

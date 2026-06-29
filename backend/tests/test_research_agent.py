import pytest

from app.core import Settings
from app.services import research_agent
from app.services.query_planner import RetrievalPlan, clear_llm_response_cache
from app.services.research_agent import (
    LLMAgentReasoner,
    ResearchAgentAction,
    ResearchAgentObservation,
    ResearchAgentService,
)


class ScriptedReasoner:
    """Deterministic stand-in for the LLM controller used in loop-mechanics tests."""

    def __init__(self, actions: list[ResearchAgentAction]) -> None:
        self._actions = list(actions)
        self.calls = 0

    def decide(self, state) -> ResearchAgentAction:
        self.calls += 1
        if self._actions:
            return self._actions.pop(0)
        return ResearchAgentAction(
            action="finalize_answer", thought_summary="No further evidence is needed."
        )


def run_to_completion(agent: ResearchAgentService, state) -> None:
    while True:
        action = agent.next_action(state)
        if action.action == "finalize_answer":
            agent.finish(state, action)
            break
        agent.observe(state, action, observation_for(action.action))


def test_loop_executes_reasoner_actions_then_finalizes() -> None:
    reasoner = ScriptedReasoner(
        [
            ResearchAgentAction(
                action="query_xbrl_metrics", thought_summary="Anchor in XBRL facts first."
            ),
            ResearchAgentAction(
                action="retrieve_mda", thought_summary="Explain the drivers with MD&A."
            ),
        ]
    )
    agent = ResearchAgentService(max_steps=5, reasoner=reasoner)
    state = agent.start(
        question="Why did Apple's margin improve last quarter?",
        plan=make_margin_plan(),
    )

    run_to_completion(agent, state)

    trace = agent.trace_payload(state)
    assert trace["mode"] == "react_llm"
    assert state.actions_taken == {"query_xbrl_metrics", "retrieve_mda"}
    assert trace["stop_reason"] == "evidence_sufficient"
    # The model's genuine thought is recorded in the step trace, not a template.
    assert any(
        step["thought_summary"] == "Explain the drivers with MD&A."
        for step in trace["steps"]
    )


def test_loop_stops_at_max_steps_even_if_reasoner_never_finalizes() -> None:
    reasoner = ScriptedReasoner(
        [
            ResearchAgentAction(action="query_xbrl_metrics", thought_summary="."),
            ResearchAgentAction(action="retrieve_mda", thought_summary="."),
            ResearchAgentAction(action="retrieve_segment_discussion", thought_summary="."),
        ]
    )
    agent = ResearchAgentService(max_steps=2, reasoner=reasoner)
    state = agent.start(
        question="Why did Apple's margin improve last quarter?",
        plan=make_margin_plan(),
    )

    run_to_completion(agent, state)

    trace = agent.trace_payload(state)
    assert trace["tool_step_count"] == 2
    assert trace["stop_reason"] == "max_steps_reached"


def test_unsupported_action_from_reasoner_raises() -> None:
    reasoner = ScriptedReasoner(
        [ResearchAgentAction(action="delete_database", thought_summary="bad")]
    )
    agent = ResearchAgentService(max_steps=5, reasoner=reasoner)
    state = agent.start(question="anything", plan=make_metric_plan())

    with pytest.raises(ValueError):
        agent.next_action(state)


def test_insufficient_evidence_reports_limitations() -> None:
    # The reasoner finalizes immediately while required roles stay empty.
    agent = ResearchAgentService(max_steps=5, reasoner=ScriptedReasoner([]))
    state = agent.start(
        question="Why did Apple's margin improve last quarter?",
        plan=make_margin_plan(),
    )

    run_to_completion(agent, state)

    trace = agent.trace_payload(state)
    assert trace["stop_reason"] == "insufficient_evidence"
    assert trace["evidence_enough"] is False
    assert "No matching XBRL metric evidence was found." in trace["limitations"]


def test_llm_reasoner_parses_action_and_surfaces_thought(monkeypatch) -> None:
    clear_llm_response_cache()
    monkeypatch.setattr(
        research_agent,
        "get_openai_client",
        lambda *args, **kwargs: FakeClient(
            ['{"thought": "MD&A explains the driver.", "action": "retrieve_mda"}']
        ),
    )
    reasoner = LLMAgentReasoner(make_settings())
    state = ResearchAgentService(reasoner=reasoner).start(
        question="Why did Apple's margin improve last quarter?",
        plan=make_margin_plan(),
    )

    action = reasoner.decide(state)

    assert action.action == "retrieve_mda"
    assert action.thought_summary == "MD&A explains the driver."


def test_llm_reasoner_rejects_out_of_vocabulary_action(monkeypatch) -> None:
    clear_llm_response_cache()
    client = FakeClient(['{"thought": "x", "action": "rm -rf"}'])
    monkeypatch.setattr(research_agent, "get_openai_client", lambda *a, **k: client)
    reasoner = LLMAgentReasoner(make_settings())
    state = ResearchAgentService(reasoner=reasoner).start(
        question="anything", plan=make_metric_plan()
    )

    with pytest.raises(ValueError):
        reasoner.decide(state)
    # Retried once before giving up.
    assert client.chat.completions.calls == 2


def test_llm_reasoner_requires_api_key() -> None:
    reasoner = LLMAgentReasoner(Settings(_env_file=None, openai_api_key=None))
    state = ResearchAgentService(reasoner=ScriptedReasoner([])).start(
        question="anything", plan=make_metric_plan()
    )

    with pytest.raises(RuntimeError):
        reasoner.decide(state)


# --- fakes / fixtures -------------------------------------------------------


class FakeCompletions:
    def __init__(self, contents: list[str]) -> None:
        self._contents = list(contents)
        self.calls = 0

    def create(self, **kwargs):
        content = self._contents[min(self.calls, len(self._contents) - 1)]
        self.calls += 1
        return FakeCompletion(content)


class FakeChat:
    def __init__(self, contents: list[str]) -> None:
        self.completions = FakeCompletions(contents)


class FakeClient:
    def __init__(self, contents: list[str]) -> None:
        self.chat = FakeChat(contents)


class FakeCompletion:
    def __init__(self, content: str) -> None:
        self.choices = [FakeChoice(content)]


class FakeChoice:
    def __init__(self, content: str) -> None:
        self.message = FakeMessage(content)


class FakeMessage:
    def __init__(self, content: str) -> None:
        self.content = content


def make_settings() -> Settings:
    return Settings(_env_file=None, openai_api_key="sk-test")


def observation_for(action: str) -> ResearchAgentObservation:
    counts_by_action = {
        "query_xbrl_metrics": {"facts": 4, "metric_observations": 1, "metric_comparisons": 1},
        "retrieve_mda": {"mda_explanation_chunks": 1, "mda_explanation_spans": 1},
        "retrieve_segment_discussion": {
            "segment_or_product_breakdown_chunks": 1,
            "segment_or_product_breakdown_spans": 1,
        },
        "retrieve_filing_chunks": {"primary_financial_statement_chunks": 1},
        "retrieve_risk_factors": {"risk_factor_chunks": 1, "risk_factor_spans": 1},
        "retrieve_prior_filings": {"metric_comparisons": 1},
    }
    counts = counts_by_action.get(action, {})
    return ResearchAgentObservation(
        observation_summary=f"observation for {action}",
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

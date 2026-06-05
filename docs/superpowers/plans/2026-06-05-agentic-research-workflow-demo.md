# Agentic Research Workflow Demo Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a backend-first auditable research-run contract and a minimal frontend trace viewer for the agentic SEC/XBRL research workflow.

**Architecture:** Add a `ResearchRunService` that reuses the existing retrieval and answer services, then normalizes their planner, agent, evidence, validation, and diagnostics output into one stable run response. Add a small trace builder for conversion logic and update the React research view to call `/research/runs` and render the returned timeline, evidence, and diagnostics.

**Tech Stack:** FastAPI, Pydantic, SQLAlchemy session dependency, pytest, React, TypeScript, Vite.

---

## File Structure

- Create `backend/app/schemas/research_run.py`: Pydantic models for `ResearchRunRead`, steps, evidence, and diagnostics.
- Modify `backend/app/schemas/__init__.py`: export the new research-run schemas.
- Create `backend/app/services/research_trace.py`: pure trace-normalization helpers that convert retrieval and answer responses into run steps, evidence, and diagnostics.
- Create `backend/app/services/research_run.py`: orchestration service for `POST /research/runs`.
- Modify `backend/app/services/answer_generation.py`: add `answer_from_retrieval_response()` so `/research/runs` can retrieve once and answer from the same evidence.
- Modify `backend/app/services/__init__.py`: export `ResearchRunService`.
- Modify `backend/app/api/routes/research.py`: add `POST /research/runs`.
- Create `backend/tests/test_research_run_trace.py`: unit tests for trace normalization.
- Create `backend/tests/test_research_run_service.py`: service tests with fake retriever and answer generator.
- Modify `backend/tests/test_research_api.py`: endpoint contract test for `/research/runs`.
- Modify `frontend/src/api/sec.ts`: add research-run TypeScript types and `runResearch()`.
- Modify `frontend/src/App.tsx`: switch research view state to `ResearchRunResponse`, call `runResearch()`, and render answer, timeline, evidence, and diagnostics.
- Modify `frontend/src/styles.css`: add dense trace-viewer styles.

## Implementation Notes

- Do not add research-run persistence in this milestone.
- Do not introduce an external tracing vendor in this milestone.
- Keep full chain-of-thought out of the API. Use existing concise `thought_summary` strings.
- Keep `/research/query` working by delegating to the new answer-from-retrieval method.
- Protect existing dirty worktree changes. Stage only files touched by the current task before each commit.

---

### Task 1: Backend Research Run Schemas

**Files:**
- Create: `backend/app/schemas/research_run.py`
- Modify: `backend/app/schemas/__init__.py`
- Test: `backend/tests/test_research_run_trace.py`

- [ ] **Step 1: Write failing schema test**

Create `backend/tests/test_research_run_trace.py` with this first test:

```python
from app.schemas import (
    ResearchRunDiagnosticsRead,
    ResearchRunEvidenceRead,
    ResearchRunRead,
    ResearchRunStepRead,
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
```

- [ ] **Step 2: Run schema test and verify it fails**

Run:

```bash
cd backend
./.venv/bin/pytest tests/test_research_run_trace.py::test_research_run_schema_serializes_minimal_contract -q
```

Expected: FAIL with an import error for `ResearchRunRead` or related new schema names.

- [ ] **Step 3: Create research-run schemas**

Create `backend/app/schemas/research_run.py`:

```python
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from app.schemas.answer import AnswerCitationRead, CitationValidationRead


RESEARCH_RUN_CONTRACT_VERSION = "research_run.v1"


class ResearchRunStepRead(BaseModel):
    step_id: str
    step_index: int
    phase: str
    name: str
    status: Literal["completed", "failed", "degraded"]
    summary: str
    tool_name: str | None = None
    tool_input_summary: dict[str, Any] | None = None
    evidence_ids: list[str] = Field(default_factory=list)
    started_at: str | None = None
    finished_at: str | None = None
    duration_ms: float | None = None
    degraded_reason: str | None = None


class ResearchRunEvidenceRead(BaseModel):
    evidence_id: str
    evidence_type: str
    role: str
    title: str
    text: str | None = None
    metric_key: str | None = None
    value: str | None = None
    period: str | None = None
    form_type: str | None = None
    filing_date: str | None = None
    section: str | None = None
    sec_url: str | None = None
    source_ids: dict[str, Any] = Field(default_factory=dict)


class ResearchRunDiagnosticsRead(BaseModel):
    candidate_counts: dict[str, int] = Field(default_factory=dict)
    timing_ms: dict[str, float] = Field(default_factory=dict)
    degraded: list[dict[str, str]] = Field(default_factory=list)
    retrieval_config: dict[str, Any] = Field(default_factory=dict)
    source_coverage_summary: dict[str, Any] = Field(default_factory=dict)
    top_score_breakdown: list[dict[str, Any]] = Field(default_factory=list)


class ResearchRunRead(BaseModel):
    run_id: str
    contract_version: Literal["research_run.v1"] = RESEARCH_RUN_CONTRACT_VERSION
    status: Literal["completed", "failed", "insufficient_evidence"]
    ticker: str
    question: str
    started_at: str | None = None
    finished_at: str | None = None
    duration_ms: float | None = None
    answer: str
    citations: list[AnswerCitationRead] = Field(default_factory=list)
    validation_status: Literal["passed", "failed", "insufficient_evidence"]
    validation: CitationValidationRead | dict[str, Any]
    limitations: list[str] = Field(default_factory=list)
    plan: dict[str, Any]
    steps: list[ResearchRunStepRead] = Field(default_factory=list)
    evidence: list[ResearchRunEvidenceRead] = Field(default_factory=list)
    diagnostics: ResearchRunDiagnosticsRead = Field(default_factory=ResearchRunDiagnosticsRead)
```

- [ ] **Step 4: Export schemas**

Modify `backend/app/schemas/__init__.py`:

```python
from app.schemas.research_run import (
    RESEARCH_RUN_CONTRACT_VERSION,
    ResearchRunDiagnosticsRead,
    ResearchRunEvidenceRead,
    ResearchRunRead,
    ResearchRunStepRead,
)
```

Add these names to `__all__`:

```python
    "RESEARCH_RUN_CONTRACT_VERSION",
    "ResearchRunDiagnosticsRead",
    "ResearchRunEvidenceRead",
    "ResearchRunRead",
    "ResearchRunStepRead",
```

- [ ] **Step 5: Run schema test and verify it passes**

Run:

```bash
cd backend
./.venv/bin/pytest tests/test_research_run_trace.py::test_research_run_schema_serializes_minimal_contract -q
```

Expected: PASS.

- [ ] **Step 6: Commit Task 1**

Run:

```bash
git add backend/app/schemas/research_run.py backend/app/schemas/__init__.py backend/tests/test_research_run_trace.py
git commit -m "feat: add research run schemas"
```

---

### Task 2: Trace Builder For Steps, Evidence, And Diagnostics

**Files:**
- Create: `backend/app/services/research_trace.py`
- Modify: `backend/tests/test_research_run_trace.py`

- [ ] **Step 1: Add failing trace builder tests**

Append these tests to `backend/tests/test_research_run_trace.py`:

```python
from datetime import date
from decimal import Decimal

from app.schemas import (
    AnswerCitationRead,
    CitationValidationRead,
    EvidencePackRead,
    EvidenceSpanRead,
    MetricComparisonRead,
    MetricObservationRead,
    ResearchAnswerResponseRead,
    RetrievalPlanRead,
    RetrievalResponse,
)
from app.services.research_trace import (
    build_research_run_diagnostics,
    build_research_run_evidence,
    build_research_run_steps,
)


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
    assert evidence_by_id["metric_comparison:revenue:latest_quarter_yoy:100:90"].role == "metric_comparison"
    assert evidence_by_id["span:10:mda_explanation_chunks:0:50"].section == "Management's Discussion and Analysis"


def test_trace_builder_copies_diagnostics() -> None:
    diagnostics = build_research_run_diagnostics(make_retrieval_response())

    assert diagnostics.candidate_counts["dense"] == 3
    assert diagnostics.timing_ms["total_ms"] == 12.0
    assert diagnostics.degraded[0]["stage"] == "dense"
```

- [ ] **Step 2: Run trace builder tests and verify they fail**

Run:

```bash
cd backend
./.venv/bin/pytest tests/test_research_run_trace.py -q
```

Expected: FAIL with import error for `app.services.research_trace`.

- [ ] **Step 3: Implement trace builder**

Create `backend/app/services/research_trace.py`:

```python
from __future__ import annotations

from decimal import Decimal
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
        top_score_breakdown=_top_score_breakdown(retrieval_response),
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
```

- [ ] **Step 4: Run trace builder tests and verify they pass**

Run:

```bash
cd backend
./.venv/bin/pytest tests/test_research_run_trace.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit Task 2**

Run:

```bash
git add backend/app/services/research_trace.py backend/tests/test_research_run_trace.py
git commit -m "feat: normalize research run traces"
```

---

### Task 3: Answer Service Single-Retrieval Entry Point

**Files:**
- Modify: `backend/app/services/answer_generation.py`
- Modify: `backend/tests/test_answer_generation.py`

- [ ] **Step 1: Add failing test for answering from an existing retrieval response**

Append to `backend/tests/test_answer_generation.py`:

```python
def test_research_answer_service_answers_from_existing_retrieval_response() -> None:
    generator = SequenceAnswerGenerator(
        [
            GeneratedAnswer(
                answer=(
                    "Total net sales were supported by the selected filing span. "
                    "[span:101:primary_financial_statement_chunks:0:80]"
                ),
                cited_evidence_ids=["span:101:primary_financial_statement_chunks:0:80"],
            ),
        ]
    )
    service = ResearchAnswerService(
        None,
        retriever=FakeRetriever(),
        answer_generator=generator,
    )

    response = service.answer_from_retrieval_response(make_request(), make_response())

    assert response.validation_status == "passed"
    assert response.retrieval_plan.question_type == "metric"
    assert generator.call_count == 1
```

- [ ] **Step 2: Run test and verify it fails**

Run:

```bash
cd backend
./.venv/bin/pytest tests/test_answer_generation.py::test_research_answer_service_answers_from_existing_retrieval_response -q
```

Expected: FAIL with `AttributeError: 'ResearchAnswerService' object has no attribute 'answer_from_retrieval_response'`.

- [ ] **Step 3: Refactor answer service**

In `backend/app/services/answer_generation.py`, replace `ResearchAnswerService.answer()` with a delegating method and add `answer_from_retrieval_response()`:

```python
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
```

- [ ] **Step 4: Run answer generation tests**

Run:

```bash
cd backend
./.venv/bin/pytest tests/test_answer_generation.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit Task 3**

Run:

```bash
git add backend/app/services/answer_generation.py backend/tests/test_answer_generation.py
git commit -m "refactor: answer from existing retrieval evidence"
```

---

### Task 4: ResearchRunService

**Files:**
- Create: `backend/app/services/research_run.py`
- Modify: `backend/app/services/__init__.py`
- Create: `backend/tests/test_research_run_service.py`

- [ ] **Step 1: Write failing service tests**

Create `backend/tests/test_research_run_service.py`:

```python
from app.schemas import RetrievalRequest
from app.services.research_run import ResearchRunService

from .test_answer_generation import SequenceAnswerGenerator
from .test_answer_context import make_response
from app.services.answer_generation import GeneratedAnswer


class FakeRunRetriever:
    def __init__(self):
        self.call_count = 0

    def retrieve(self, request):
        self.call_count += 1
        return make_response()


def test_research_run_service_returns_auditable_completed_run() -> None:
    retriever = FakeRunRetriever()
    generator = SequenceAnswerGenerator(
        [
            GeneratedAnswer(
                answer=(
                    "Revenue was supported by selected evidence. "
                    "[span:101:primary_financial_statement_chunks:0:80]"
                ),
                cited_evidence_ids=["span:101:primary_financial_statement_chunks:0:80"],
            )
        ]
    )
    service = ResearchRunService(
        None,
        retriever=retriever,
        answer_generator=generator,
    )

    run = service.run(RetrievalRequest(ticker="AAPL", question="What was revenue?"))

    assert run.contract_version == "research_run.v1"
    assert run.status == "completed"
    assert run.validation_status == "passed"
    assert run.run_id.startswith("run_")
    assert run.steps
    assert run.evidence
    assert retriever.call_count == 1


def test_research_run_service_preserves_insufficient_evidence_status() -> None:
    class EmptyRetriever:
        def retrieve(self, request):
            response = make_response()
            response.final_evidence_pack = response.final_evidence_pack.model_copy(
                update={
                    "metric_observations": [],
                    "metric_comparisons": [],
                    "primary_financial_statement_chunks": [],
                    "primary_financial_statement_spans": [],
                }
            )
            response.retrieved_facts = []
            return response

    service = ResearchRunService(
        None,
        retriever=EmptyRetriever(),
        answer_generator=SequenceAnswerGenerator([]),
    )

    run = service.run(RetrievalRequest(ticker="AAPL", question="Unsupported?"))

    assert run.status == "insufficient_evidence"
    assert run.validation_status == "insufficient_evidence"
    assert run.limitations
    assert run.diagnostics.source_coverage_summary
```

- [ ] **Step 2: Run service tests and verify they fail**

Run:

```bash
cd backend
./.venv/bin/pytest tests/test_research_run_service.py -q
```

Expected: FAIL with import error for `app.services.research_run`.

- [ ] **Step 3: Implement ResearchRunService**

Create `backend/app/services/research_run.py`:

```python
from __future__ import annotations

from datetime import UTC, datetime
from time import perf_counter
from uuid import uuid4

from sqlalchemy.orm import Session

from app.core import Settings, get_settings
from app.schemas import RetrievalRequest, RetrievalResponse
from app.schemas.research_run import ResearchRunRead
from app.services.answer_generation import AnswerGenerator, ResearchAnswerService
from app.services.research_trace import (
    build_research_run_diagnostics,
    build_research_run_evidence,
    build_research_run_steps,
)
from app.services.retrieval import RetrievalService


class ResearchRunService:
    def __init__(
        self,
        db: Session | None,
        *,
        settings: Settings | None = None,
        retriever=None,
        answer_generator: AnswerGenerator | None = None,
        validator=None,
    ) -> None:
        self._settings = settings or get_settings()
        self._retriever = retriever or RetrievalService(db, settings=self._settings)
        self._answer_service = ResearchAnswerService(
            db,
            settings=self._settings,
            retriever=self._retriever,
            answer_generator=answer_generator,
            validator=validator,
        )

    def run(self, request: RetrievalRequest) -> ResearchRunRead:
        run_started = perf_counter()
        started_at = datetime.now(UTC)
        run_id = f"run_{uuid4().hex}"

        retrieval_response = RetrievalResponse.model_validate(
            self._retriever.retrieve(request)
        )
        answer_response = self._answer_service.answer_from_retrieval_response(
            request,
            retrieval_response,
        )
        finished_at = datetime.now(UTC)
        duration_ms = (perf_counter() - run_started) * 1000

        return ResearchRunRead(
            run_id=run_id,
            status=_run_status(answer_response.validation_status),
            ticker=request.ticker.strip().upper(),
            question=request.question,
            started_at=started_at.isoformat(),
            finished_at=finished_at.isoformat(),
            duration_ms=duration_ms,
            answer=answer_response.answer,
            citations=answer_response.citations,
            validation_status=answer_response.validation_status,
            validation=answer_response.validation,
            limitations=answer_response.limitations,
            plan=answer_response.retrieval_plan.model_dump(mode="json"),
            steps=build_research_run_steps(retrieval_response, answer_response),
            evidence=build_research_run_evidence(retrieval_response, answer_response),
            diagnostics=build_research_run_diagnostics(retrieval_response),
        )


def _run_status(validation_status: str) -> str:
    if validation_status == "insufficient_evidence":
        return "insufficient_evidence"
    if validation_status == "failed":
        return "failed"
    return "completed"
```

- [ ] **Step 4: Export service**

Modify `backend/app/services/__init__.py`:

```python
from app.services.research_run import ResearchRunService
```

Add to `__all__`:

```python
    "ResearchRunService",
```

- [ ] **Step 5: Run service tests**

Run:

```bash
cd backend
./.venv/bin/pytest tests/test_research_run_service.py tests/test_research_run_trace.py -q
```

Expected: PASS.

- [ ] **Step 6: Commit Task 4**

Run:

```bash
git add backend/app/services/research_run.py backend/app/services/__init__.py backend/tests/test_research_run_service.py
git commit -m "feat: add auditable research run service"
```

---

### Task 5: Research Runs API Endpoint

**Files:**
- Modify: `backend/app/api/routes/research.py`
- Modify: `backend/tests/test_research_api.py`

- [ ] **Step 1: Add failing API test**

Append to `backend/tests/test_research_api.py`:

```python
def test_research_runs_endpoint_returns_auditable_run(monkeypatch) -> None:
    from app.api.routes import research as research_routes
    from app.schemas import ResearchRunDiagnosticsRead, ResearchRunRead, ResearchRunStepRead

    class FakeResearchRunService:
        def __init__(self, db):
            self.db = db

        def run(self, request):
            return ResearchRunRead(
                run_id="run-api",
                status="completed",
                ticker=request.ticker,
                question=request.question,
                answer="AAPL answer. [financial_fact:501]",
                citations=[],
                validation_status="passed",
                validation={"status": "passed", "cited_evidence_ids": []},
                limitations=[],
                plan={"question_type": "metric"},
                steps=[
                    ResearchRunStepRead(
                        step_id="step-1",
                        step_index=0,
                        phase="planning",
                        name="Plan query",
                        status="completed",
                        summary="Planned query.",
                    )
                ],
                evidence=[],
                diagnostics=ResearchRunDiagnosticsRead(),
            )

    monkeypatch.setattr(research_routes, "ResearchRunService", FakeResearchRunService)

    response = client.post(
        "/research/runs",
        json={"ticker": "AAPL", "question": "What was revenue?"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["contract_version"] == "research_run.v1"
    assert body["run_id"] == "run-api"
    assert body["steps"][0]["phase"] == "planning"
```

- [ ] **Step 2: Run API test and verify it fails**

Run:

```bash
cd backend
./.venv/bin/pytest tests/test_research_api.py::test_research_runs_endpoint_returns_auditable_run -q
```

Expected: FAIL with 404 for `/research/runs` or import error for `ResearchRunService`.

- [ ] **Step 3: Add endpoint**

Modify imports in `backend/app/api/routes/research.py`:

```python
from app.schemas import (
    QueryPlanRequest,
    ResearchAnswerResponseRead,
    ResearchRunRead,
    RetrievalAnalysisResponse,
    RetrievalPlanRead,
    RetrievalRequest,
    RetrievalResponse,
)
```

Add `ResearchRunService` to service imports:

```python
from app.services import (
    QueryPlanner,
    ResearchAnswerService,
    ResearchRunService,
    RetrievalCompanyNotFoundError,
    RetrievalError,
    RetrievalService,
)
```

Add endpoint after `/query`:

```python
@router.post("/runs", response_model=ResearchRunRead)
def run_research(
    request: RetrievalRequest,
    db: Session = Depends(get_db_session),
) -> ResearchRunRead:
    try:
        return ResearchRunService(db).run(request)
    except RetrievalCompanyNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RetrievalError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
```

- [ ] **Step 4: Run API tests**

Run:

```bash
cd backend
./.venv/bin/pytest tests/test_research_api.py::test_research_runs_endpoint_returns_auditable_run -q
```

Expected: PASS.

- [ ] **Step 5: Run backend research-related tests**

Run:

```bash
cd backend
./.venv/bin/pytest tests/test_research_api.py tests/test_answer_generation.py tests/test_research_run_service.py tests/test_research_run_trace.py -q
```

Expected: PASS.

- [ ] **Step 6: Commit Task 5**

Run:

```bash
git add backend/app/api/routes/research.py backend/tests/test_research_api.py
git commit -m "feat: expose research run audit endpoint"
```

---

### Task 6: Frontend API Types And Research Run Request

**Files:**
- Modify: `frontend/src/api/sec.ts`

- [ ] **Step 1: Add research-run TypeScript types**

Modify `frontend/src/api/sec.ts` after `ResearchAnswerResponse`:

```ts
export type ResearchRunStep = {
  step_id: string;
  step_index: number;
  phase: string;
  name: string;
  status: "completed" | "failed" | "degraded";
  summary: string;
  tool_name: string | null;
  tool_input_summary: Record<string, unknown> | null;
  evidence_ids: string[];
  started_at: string | null;
  finished_at: string | null;
  duration_ms: number | null;
  degraded_reason: string | null;
};

export type ResearchRunEvidence = {
  evidence_id: string;
  evidence_type: string;
  role: string;
  title: string;
  text: string | null;
  metric_key: string | null;
  value: string | null;
  period: string | null;
  form_type: string | null;
  filing_date: string | null;
  section: string | null;
  sec_url: string | null;
  source_ids: Record<string, unknown>;
};

export type ResearchRunDiagnostics = {
  candidate_counts: Record<string, number>;
  timing_ms: Record<string, number>;
  degraded: { stage: string; reason: string }[];
  retrieval_config: Record<string, unknown>;
  source_coverage_summary: Record<string, unknown>;
  top_score_breakdown: Record<string, unknown>[];
};

export type ResearchRunResponse = {
  run_id: string;
  contract_version: "research_run.v1";
  status: "completed" | "failed" | "insufficient_evidence";
  ticker: string;
  question: string;
  started_at: string | null;
  finished_at: string | null;
  duration_ms: number | null;
  answer: string;
  citations: AnswerCitation[];
  validation_status: "passed" | "failed" | "insufficient_evidence";
  validation: CitationValidation;
  limitations: string[];
  plan: RetrievalPlan;
  steps: ResearchRunStep[];
  evidence: ResearchRunEvidence[];
  diagnostics: ResearchRunDiagnostics;
};
```

- [ ] **Step 2: Add runResearch API function**

Add below `queryResearch()`:

```ts
export function runResearch(request: {
  ticker: string;
  question: string;
  form_type?: string;
  section?: string;
}): Promise<ResearchRunResponse> {
  return requestJson<ResearchRunResponse>("/research/runs", {
    body: JSON.stringify(request),
    headers: { "Content-Type": "application/json" },
    method: "POST",
  });
}
```

- [ ] **Step 3: Type-check frontend**

Run:

```bash
cd frontend
npm run build
```

Expected: PASS.

- [ ] **Step 4: Commit Task 6**

Run:

```bash
git add frontend/src/api/sec.ts
git commit -m "feat: add research run frontend API types"
```

---

### Task 7: Frontend Trace Viewer

**Files:**
- Modify: `frontend/src/App.tsx`
- Modify: `frontend/src/styles.css`

- [ ] **Step 1: Update imports and state**

In `frontend/src/App.tsx`, update imports from `./api/sec`:

```ts
  ResearchRunEvidence,
  ResearchRunResponse,
  ResearchRunStep,
  runResearch,
```

Remove `ResearchAnswerResponse` and `queryResearch` from the import list.

Change answer state:

```ts
  const [researchRun, setResearchRun] = useState<ResearchRunResponse | null>(null);
  const [selectedRunStepId, setSelectedRunStepId] = useState<string | null>(null);
```

Remove the old `answerResult` state.

- [ ] **Step 2: Update submit handler**

Replace `handleRetrieveEvidence()` body that calls `queryResearch()` with:

```ts
    try {
      const run = await runResearch({
        ticker: company.ticker,
        question,
      });
      setResearchRun(run);
      setSelectedRunStepId(run.steps[0]?.step_id ?? null);
    } catch (retrievalError) {
      setResearchRun(null);
      setSelectedRunStepId(null);
      setError(getErrorMessage(retrievalError));
    } finally {
      setIsAsking(false);
    }
```

- [ ] **Step 3: Pass run props to ResearchPage**

Update the `ResearchPage` call:

```tsx
          <ResearchPage
            ticker={company?.ticker ?? ticker.trim().toUpperCase()}
            hasCompany={company !== null}
            question={researchQuestion}
            run={researchRun}
            selectedStepId={selectedRunStepId}
            isAsking={isAsking}
            onQuestionChange={setResearchQuestion}
            onSelectStep={setSelectedRunStepId}
            onSubmit={handleRetrieveEvidence}
          />
```

- [ ] **Step 4: Update ResearchPage props**

Change `ResearchPage` signature:

```tsx
function ResearchPage({
  ticker,
  hasCompany,
  question,
  run,
  selectedStepId,
  isAsking,
  onQuestionChange,
  onSelectStep,
  onSubmit,
}: {
  ticker: string;
  hasCompany: boolean;
  question: string;
  run: ResearchRunResponse | null;
  selectedStepId: string | null;
  isAsking: boolean;
  onQuestionChange: (question: string) => void;
  onSelectStep: (stepId: string) => void;
  onSubmit: (event: FormEvent<HTMLFormElement>) => void | Promise<void>;
}) {
```

Inside `ResearchPage`, replace `result` references with `run`. Keep the existing cited answer/evidence sections by using:

```ts
  const plan = run?.plan;
  const answerResult = run;
```

Then update existing accessors from `result` to `answerResult` where they read answer, citations, limitations, validation status, and plan.

- [ ] **Step 5: Add timeline and selected evidence helpers**

Add these helpers near existing frontend helper functions:

```tsx
function getSelectedStep(
  steps: ResearchRunStep[],
  selectedStepId: string | null,
): ResearchRunStep | null {
  return (
    steps.find((step) => step.step_id === selectedStepId) ??
    steps[0] ??
    null
  );
}

function getStepEvidence(
  evidence: ResearchRunEvidence[],
  step: ResearchRunStep | null,
): ResearchRunEvidence[] {
  if (!step) {
    return [];
  }
  const ids = new Set(step.evidence_ids);
  return evidence.filter((item) => ids.has(item.evidence_id));
}

function formatRunDuration(durationMs: number | null): string {
  if (durationMs === null) {
    return "n/a";
  }
  if (durationMs < 1000) {
    return `${Math.round(durationMs)} ms`;
  }
  return `${(durationMs / 1000).toFixed(2)} s`;
}
```

- [ ] **Step 6: Render the audit layout**

Inside `ResearchPage`, after the form and before the existing evidence sections, add:

```tsx
      {run && (
        <div className="research-audit-grid">
          <section className="answer-panel" aria-label="Final answer">
            <div className="panel-header panel-header--compact">
              <h3>Answer</h3>
              <span className={`validation-pill validation-pill--${run.validation_status}`}>
                {run.validation_status}
              </span>
            </div>
            <CitedAnswer answer={run.answer} citationNumberById={citationNumberById} />
            {showLimitations && (
              <details className="limitations-panel" open={keepLimitationsOpen}>
                <summary>Limitations</summary>
                <ul>
                  {run.limitations.map((limitation) => (
                    <li key={limitation}>{limitation}</li>
                  ))}
                </ul>
              </details>
            )}
          </section>

          <section className="trace-panel" aria-label="Agent trace">
            <div className="panel-header panel-header--compact">
              <h3>Agent Trace</h3>
              <span>{formatRunDuration(run.duration_ms)}</span>
            </div>
            <div className="trace-list">
              {run.steps.map((step) => (
                <button
                  className={`trace-step ${
                    step.step_id === selectedStep?.step_id ? "trace-step--active" : ""
                  }`}
                  key={step.step_id}
                  type="button"
                  onClick={() => onSelectStep(step.step_id)}
                >
                  <span>{step.phase}</span>
                  <strong>{step.name}</strong>
                  <small>{step.summary}</small>
                </button>
              ))}
            </div>
          </section>

          <section className="evidence-panel" aria-label="Selected step evidence">
            <div className="panel-header panel-header--compact">
              <h3>Step Evidence</h3>
              <span>{selectedEvidence.length}</span>
            </div>
            <EvidenceCards evidence={selectedEvidence.length ? selectedEvidence : run.evidence.slice(0, 6)} />
          </section>
        </div>
      )}
```

At the top of `ResearchPage`, define:

```ts
  const selectedStep = getSelectedStep(run?.steps ?? [], selectedStepId);
  const selectedEvidence = getStepEvidence(run?.evidence ?? [], selectedStep);
```

- [ ] **Step 7: Add EvidenceCards component**

Add below `ResearchPage`:

```tsx
function EvidenceCards({ evidence }: { evidence: ResearchRunEvidence[] }) {
  if (evidence.length === 0) {
    return <p className="empty-state">No evidence attached to this step.</p>;
  }

  return (
    <div className="run-evidence-list">
      {evidence.map((item) => (
        <article className="run-evidence-card" key={item.evidence_id}>
          <div className="chunk-meta">
            <span>{item.evidence_type}</span>
            <span>{item.role}</span>
            {item.form_type && <span>{item.form_type}</span>}
          </div>
          <h4>{item.title}</h4>
          {item.text && <p>{item.text}</p>}
          <div className="chunk-meta chunk-meta--subtle">
            {item.period && <span>{item.period}</span>}
            {item.section && <span>{item.section}</span>}
            {item.filing_date && <span>{item.filing_date}</span>}
          </div>
          {item.sec_url && (
            <a href={item.sec_url} target="_blank" rel="noreferrer">
              SEC Source
            </a>
          )}
        </article>
      ))}
    </div>
  );
}
```

- [ ] **Step 8: Add diagnostics panel**

Inside `ResearchPage`, after the audit grid, add:

```tsx
      {run && (
        <details className="diagnostics-panel">
          <summary>Diagnostics</summary>
          <pre>
            {JSON.stringify(
              {
                candidate_counts: run.diagnostics.candidate_counts,
                timing_ms: run.diagnostics.timing_ms,
                degraded: run.diagnostics.degraded,
                retrieval_config: run.diagnostics.retrieval_config,
                source_coverage_summary: run.diagnostics.source_coverage_summary,
              },
              null,
              2,
            )}
          </pre>
        </details>
      )}
```

- [ ] **Step 9: Add CSS**

Append to `frontend/src/styles.css`:

```css
.research-audit-grid {
  display: grid;
  grid-template-columns: minmax(0, 1.1fr) minmax(240px, 0.9fr) minmax(260px, 1fr);
  gap: 16px;
  margin-top: 18px;
}

.answer-panel,
.trace-panel,
.evidence-panel,
.diagnostics-panel {
  min-width: 0;
  border: 1px solid #d6dee8;
  border-radius: 8px;
  background: #ffffff;
  padding: 14px;
}

.validation-pill {
  border-radius: 999px;
  padding: 4px 9px;
  background: #eef4fb;
  color: #2459b3;
  font-size: 0.74rem;
  font-weight: 800;
}

.validation-pill--passed {
  background: #e7f6ef;
  color: #166343;
}

.validation-pill--failed,
.validation-pill--insufficient_evidence {
  background: #fff1e8;
  color: #9a4b13;
}

.trace-list,
.run-evidence-list {
  display: grid;
  gap: 10px;
}

.trace-step {
  display: grid;
  width: 100%;
  gap: 3px;
  border: 1px solid #d6dee8;
  border-radius: 8px;
  padding: 10px;
  background: #ffffff;
  text-align: left;
}

.trace-step span {
  color: #52606f;
  font-size: 0.72rem;
  font-weight: 800;
  text-transform: uppercase;
}

.trace-step strong {
  color: #1f2933;
  font-size: 0.88rem;
}

.trace-step small {
  color: #52606f;
  line-height: 1.35;
}

.trace-step--active {
  border-color: #2459b3;
  background: #eef4fb;
}

.run-evidence-card {
  border: 1px solid #e3e9f0;
  border-radius: 8px;
  padding: 12px;
}

.run-evidence-card h4 {
  margin: 6px 0;
  font-size: 0.9rem;
}

.run-evidence-card p {
  color: #334155;
  font-size: 0.86rem;
}

.diagnostics-panel {
  margin-top: 16px;
}

.diagnostics-panel pre {
  max-height: 280px;
  overflow: auto;
  border-radius: 6px;
  background: #0f172a;
  color: #e2e8f0;
  padding: 12px;
  font-size: 0.78rem;
}

@media (max-width: 1180px) {
  .research-audit-grid {
    grid-template-columns: 1fr;
  }
}
```

- [ ] **Step 10: Type-check frontend**

Run:

```bash
cd frontend
npm run build
```

Expected: PASS.

- [ ] **Step 11: Commit Task 7**

Run:

```bash
git add frontend/src/App.tsx frontend/src/styles.css
git commit -m "feat: show research run audit trace"
```

---

### Task 8: Verification And Demo Readiness

**Files:**
- Modify: `README.md`
- Modify: `README.zh-CN.md`

- [ ] **Step 1: Add README API note**

In `README.md`, add this bullet to the implemented/current scope list:

```markdown
- Auditable research-run API and minimal frontend trace viewer for planner, agent steps, evidence, validation, and diagnostics.
```

Add this endpoint to the research API section:

```markdown
`POST /research/runs` returns the final cited answer plus an auditable `research_run.v1` contract containing planner output, agent/tool steps, normalized evidence, validation status, limitations, and retrieval diagnostics.
```

- [ ] **Step 2: Add Chinese README note**

In `README.zh-CN.md`, add:

```markdown
- 可审计 research-run API 与轻量前端 trace viewer，用于展示 planner、agent steps、证据、引用验证和检索诊断。
```

Add endpoint text:

```markdown
`POST /research/runs` 返回最终引用答案，以及 `research_run.v1` 审计结构，包含 planner 输出、agent/tool steps、标准化证据、验证状态、局限说明和检索诊断。
```

- [ ] **Step 3: Run focused backend tests**

Run:

```bash
cd backend
./.venv/bin/pytest tests/test_research_run_trace.py tests/test_research_run_service.py tests/test_research_api.py tests/test_answer_generation.py -q
```

Expected: PASS.

- [ ] **Step 4: Run frontend build**

Run:

```bash
cd frontend
npm run build
```

Expected: PASS.

- [ ] **Step 5: Run broader backend smoke tests**

Run:

```bash
cd backend
./.venv/bin/pytest tests/test_research_agent.py tests/test_retrieval.py tests/test_answer_context.py -q
```

Expected: PASS.

- [ ] **Step 6: Commit Task 8**

Run:

```bash
git add README.md README.zh-CN.md
git commit -m "docs: document research run audit workflow"
```

---

## Final Verification

- [ ] **Step 1: Check working tree**

Run:

```bash
git status --short
```

Expected: no staged files from the implementation task remain.

- [ ] **Step 2: Run final backend verification**

Run:

```bash
cd backend
./.venv/bin/pytest tests/test_research_run_trace.py tests/test_research_run_service.py tests/test_research_api.py tests/test_answer_generation.py -q
```

Expected: PASS.

- [ ] **Step 3: Run final frontend verification**

Run:

```bash
cd frontend
npm run build
```

Expected: PASS.

- [ ] **Step 4: Manual demo check**

Start backend and frontend with the existing project commands. In the Research view, load a company, ask a question, and verify that the page shows:

- final answer or insufficient-evidence response
- validation badge
- agent trace timeline
- selected-step evidence cards
- SEC source links where evidence has URLs
- diagnostics panel with candidate counts and timing

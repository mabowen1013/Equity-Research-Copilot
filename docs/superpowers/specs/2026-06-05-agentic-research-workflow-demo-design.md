# Agentic Research Workflow Demo Design

Date: 2026-06-05

## Purpose

Build the first demonstrable version of an auditable multi-agent research workflow for Equity Research Copilot. The demo should show not only the final answer, but also the evidence trail that produced it: query planning, bounded agent decisions, tool calls, retrieved SEC/XBRL evidence, answer generation, citation validation, limitations, latency, and degraded states.

The product direction is option C from the planning discussion: backend-first auditability with a minimal frontend trace viewer.

## Goals

- Turn a research query into a structured, inspectable `research_run`.
- Preserve the existing citation-first and evidence-first philosophy.
- Reuse the current SEC ingestion, XBRL facts, retrieval, evidence pack, bounded ReAct controller, and answer generation services where they are useful.
- Normalize existing debug traces into a stable run contract that can be displayed in the UI and evaluated in tests.
- Add a minimal frontend view that makes the workflow understandable as an agentic research process rather than a black-box answer.
- Keep the first version deterministic and bounded enough for finance use cases.

## Non-Goals

- Do not add buy/sell/hold recommendations, price targets, or brokerage-style investment advice.
- Do not replace deterministic SEC/XBRL/retrieval logic with unconstrained LLM reasoning.
- Do not introduce a full external agent framework in this first demo.
- Do not build a complete report generator yet.
- Do not persist research runs in the first implementation.

## Recommended Approach

Use a new research-run orchestration layer on top of the existing pipeline.

The current system already has strong pieces:

- `QueryPlanner` produces structured planning slots.
- `RetrievalService` performs dense retrieval, lexical retrieval, XBRL fact retrieval, RRF fusion, reranking, evidence span selection, evidence pack assembly, and retrieval trace output.
- `ResearchAgentService` provides a bounded ReAct-style controller with concise thought summaries instead of full chain-of-thought.
- `ResearchAnswerService` generates cited answers, validates citation IDs, and falls back to insufficient-evidence responses.

The first demo should standardize these pieces into a user-facing audit contract instead of scattering diagnostics across separate responses.

## User Experience

The user enters a ticker and question in the research UI. The system returns:

- Final answer with citation markers.
- Validation status: `passed`, `failed`, or `insufficient_evidence`.
- Limitations explaining evidence gaps.
- A visible agent timeline.
- Evidence cards for XBRL facts, metric comparisons, filing chunks, and extracted evidence spans.
- SEC source links where available.
- Retrieval and runtime diagnostics.

The minimal frontend should favor clarity over polish. A good first screen is:

- Left column: question, answer, validation badge, limitations.
- Middle column: agent timeline with planner, tool calls, retrieval, answer generation, and validation.
- Right column: evidence details for the selected timeline step.
- Bottom or collapsible section: debug metrics such as candidate counts, timing, degraded reasons, and rerank/fusion summaries.

## Backend Design

### ResearchRunService

Add a service that orchestrates a complete auditable query run.

Primary responsibility:

- Accept a `RetrievalRequest`.
- Execute the current retrieval and answer flow.
- Collect planner output, agent trace, tool calls, evidence summaries, validation results, timing, and degraded events.
- Return a normalized `ResearchRunRead`.

The service should not duplicate retrieval, answer generation, or citation validation logic. It should compose existing services and translate their traces into the stable run contract.

### Trace Builder

Add a small trace-normalization helper, such as `ResearchTraceBuilder`, to keep the orchestration readable.

Responsibilities:

- Convert `retrieval_trace.agent.steps` into agent timeline steps.
- Convert retrieval candidate counts, dense/lexical/fact sources, RRF, rerank, and evidence pack trace into tool-call or diagnostic entries.
- Convert answer validation output into final validation steps.
- Attach evidence IDs to the timeline wherever possible.
- Avoid storing full chain-of-thought. Keep `thought_summary` only.

### API

Add a run-oriented endpoint:

- `POST /research/runs`

Response:

- `ResearchRunRead`

Keep `/research/query` for the existing answer workflow. It may remain answer-focused, while `/research/runs` becomes the auditable demo endpoint.

Do not add `GET /research/runs/{run_id}` in the first implementation. The first demo should return the complete run inline from `POST /research/runs`.

## Run Contract

The core response should be stable and frontend-friendly.

```text
ResearchRunRead
- run_id
- contract_version
- status
- ticker
- question
- started_at
- finished_at
- duration_ms
- answer
- citations
- validation_status
- validation
- limitations
- plan
- steps
- evidence
- diagnostics
```

### ResearchRunStepRead

Each step should show what happened without exposing private reasoning.

```text
ResearchRunStepRead
- step_id
- step_index
- phase
- name
- status
- summary
- tool_name
- tool_input_summary
- evidence_ids
- started_at
- finished_at
- duration_ms
- degraded_reason
```

Suggested phases:

- `planning`
- `agent`
- `tool`
- `retrieval`
- `answer_generation`
- `validation`
- `finalization`

### Evidence Summary

The run should expose the evidence in a single normalized list.

```text
ResearchRunEvidenceRead
- evidence_id
- evidence_type
- role
- title
- text
- metric_key
- value
- period
- form_type
- filing_date
- section
- sec_url
- source_ids
```

This list should be built from the final evidence pack and retrieved facts. It should include chunks, spans, financial facts, metric observations, and metric comparisons.

### Diagnostics

Diagnostics should be compact and structured.

```text
ResearchRunDiagnosticsRead
- candidate_counts
- timing_ms
- degraded
- retrieval_config
- source_coverage_summary
- top_score_breakdown
```

The frontend can display these as a collapsible JSON-like audit panel.

## Agentic Behavior

The demo should make the agentic workflow visible while keeping risky behavior constrained.

The bounded agent may choose among known actions:

- `query_xbrl_metrics`
- `retrieve_filing_chunks`
- `retrieve_mda`
- `retrieve_risk_factors`
- `retrieve_segment_discussion`
- `retrieve_prior_filings`
- `finalize_answer`

Each action must map to a deterministic service call or existing retrieval route. The agent should not invent tools. If evidence remains incomplete, the run should finish with explicit limitations instead of stretching beyond the evidence.

## Error Handling

The run should remain auditable even when something fails.

- Planner failure: fall back to broad retrieval and record a degraded event.
- Dense embedding failure: continue with lexical and XBRL retrieval when possible.
- Agent failure: fall back to static planned retrieval and record a degraded event.
- Empty evidence pack: return `insufficient_evidence` with a full trace.
- Answer generation failure: use extractive fallback or insufficient-evidence response.
- Citation validation failure: retry once if the current answer service does so, then fall back safely.

No failure should silently erase the run trace.

## Frontend Design

Add a minimal trace viewer to the existing React app.

The UI should:

- Submit ticker and question to `/research/runs`.
- Show the answer and validation status.
- Render a step timeline.
- Allow selecting a step to inspect evidence IDs and summaries.
- Show evidence cards with source links.
- Show diagnostics in a compact collapsible section.

Keep styling consistent with the existing app. This is an operational research tool, so the design should be dense, restrained, and scan-friendly.

## Testing Strategy

Backend tests:

- `POST /research/runs` returns a complete run contract.
- A successful cited answer includes validation, citations, evidence, and steps.
- An insufficient-evidence response still includes planning, retrieval, validation, limitations, and diagnostics.
- Agent steps from `retrieval_trace.agent.steps` are converted into stable `ResearchRunStepRead` objects.
- Every citation in the final answer appears in the run evidence list.
- Degraded retrieval or answer fallback states are represented in diagnostics.

Frontend tests or manual checks:

- Trace viewer renders answer, validation badge, timeline, evidence cards, and diagnostics.
- Selecting a timeline step updates the evidence detail panel.
- Empty or insufficient-evidence runs are understandable to the user.

Eval additions:

- Add a small run-contract fixture eval that verifies trace completeness for representative questions:
  - pure metric question
  - metric plus MD&A driver question
  - risk factor question
  - insufficient-evidence question

## Implementation Boundaries

The first implementation should avoid broad refactors. However, it is acceptable to reshape existing code if the current boundary makes the research-run contract hard to produce or test.

Allowed targeted changes:

- Extract trace normalization from `RetrievalService`.
- Adjust `ResearchAgentService` trace payload shape if needed.
- Add schemas for run/step/evidence/diagnostics.
- Add a run endpoint and frontend API helper.
- Update the frontend research view or add a dedicated audit view.

Avoid:

- Rewriting SEC ingestion.
- Expanding XBRL metric coverage in the same milestone.
- Adding research-run persistence.
- Adding external observability vendors before the internal run contract is stable.

## Success Criteria

The demo succeeds when a user can ask a company research question and see:

- A final answer or an explicit insufficient-evidence response.
- A visible sequence of planner and agent/tool steps.
- Evidence objects tied to citations and SEC sources.
- Validation status and limitations.
- Retrieval diagnostics that explain why evidence was selected.

The system should feel like a research assistant whose work can be inspected, not a chatbot that merely sounds confident.

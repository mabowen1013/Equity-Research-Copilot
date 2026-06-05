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

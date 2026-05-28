from __future__ import annotations

from datetime import date
from typing import Any, Literal

from pydantic import BaseModel, Field

from app.schemas.retrieval import (
    EvidencePackRead,
    RetrievedFinancialFactRead,
    RetrievalRequest,
    RetrievalPlanRead,
)


ANSWER_EVIDENCE_CONTEXT_VERSION = "answer_evidence_context.v1"


class AnswerEvidenceContextRead(BaseModel):
    contract_version: Literal["answer_evidence_context.v1"] = ANSWER_EVIDENCE_CONTEXT_VERSION
    ticker: str
    question: str
    retrieval_plan: RetrievalPlanRead
    final_evidence_pack: EvidencePackRead
    retrieved_facts: list[RetrievedFinancialFactRead] = Field(default_factory=list)
    allowed_evidence_ids: list[str] = Field(default_factory=list)
    source_coverage_summary: dict[str, Any] = Field(default_factory=dict)


class ResearchQueryRequest(RetrievalRequest):
    """Public cited-Q&A request. It intentionally mirrors retrieval filters."""


class GeneratedAnswerCandidate(BaseModel):
    answer: str = Field(min_length=1)
    limitations: list[str] = Field(default_factory=list)


class AnswerCitationRead(BaseModel):
    evidence_id: str
    evidence_type: str
    source_label: str | None = None
    text: str | None = None
    sec_url: str | None = None
    form_type: str | None = None
    filing_date: date | None = None
    section: str | None = None
    pages: str | None = None
    source_ids: dict[str, Any] = Field(default_factory=dict)


class CitationValidationIssueRead(BaseModel):
    code: str
    message: str
    evidence_id: str | None = None
    sentence: str | None = None


class CitationValidationRead(BaseModel):
    status: Literal["passed", "failed"]
    cited_evidence_ids: list[str] = Field(default_factory=list)
    allowed_evidence_ids: list[str] = Field(default_factory=list)
    prompt_evidence_ids: list[str] = Field(default_factory=list)
    errors: list[CitationValidationIssueRead] = Field(default_factory=list)

    @property
    def valid(self) -> bool:
        return self.status == "passed"


class ResearchAnswerResponse(BaseModel):
    answer: str
    citations: list[AnswerCitationRead] = Field(default_factory=list)
    retrieved_evidence_ids: list[str] = Field(default_factory=list)
    prompt_evidence_ids: list[str] = Field(default_factory=list)
    validation_status: Literal["passed", "failed", "insufficient_evidence"]
    validation: CitationValidationRead
    limitations: list[str] = Field(default_factory=list)
    source_coverage_summary: dict[str, Any] = Field(default_factory=dict)
    retrieval_plan: RetrievalPlanRead
    final_evidence_pack: EvidencePackRead

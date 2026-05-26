from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from app.schemas.retrieval import (
    EvidencePackRead,
    RetrievedFinancialFactRead,
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

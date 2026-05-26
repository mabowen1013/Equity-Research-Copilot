from __future__ import annotations

from app.schemas.answer import AnswerEvidenceContextRead
from app.schemas.retrieval import (
    EvidencePackRead,
    RetrievalRequest,
    RetrievalResponse,
)


def build_answer_evidence_context(
    request: RetrievalRequest,
    response: RetrievalResponse,
) -> AnswerEvidenceContextRead:
    return AnswerEvidenceContextRead(
        ticker=request.ticker.strip().upper(),
        question=request.question,
        retrieval_plan=response.retrieval_plan,
        final_evidence_pack=response.final_evidence_pack,
        retrieved_facts=response.retrieved_facts,
        allowed_evidence_ids=collect_answer_evidence_ids(response),
        source_coverage_summary=response.source_coverage_summary,
    )


def collect_answer_evidence_ids(response: RetrievalResponse) -> list[str]:
    ids = [
        *collect_evidence_pack_ids(response.final_evidence_pack),
        *(fact.evidence_id for fact in response.retrieved_facts),
    ]
    return list(dict.fromkeys(ids))


def collect_evidence_pack_ids(pack: EvidencePackRead) -> list[str]:
    ids = [
        *(comparison.evidence_id for comparison in pack.metric_comparisons),
        *(chunk.evidence_id for chunk in pack.primary_financial_statement_chunks),
        *(chunk.evidence_id for chunk in pack.mda_explanation_chunks),
        *(chunk.evidence_id for chunk in pack.segment_or_product_breakdown_chunks),
        *(chunk.evidence_id for chunk in pack.risk_factor_chunks),
        *(chunk.evidence_id for chunk in pack.annual_context_chunks),
        *(span.evidence_id for span in pack.primary_financial_statement_spans),
        *(span.evidence_id for span in pack.mda_explanation_spans),
        *(span.evidence_id for span in pack.segment_or_product_breakdown_spans),
        *(span.evidence_id for span in pack.risk_factor_spans),
        *(span.evidence_id for span in pack.annual_context_spans),
    ]
    return list(dict.fromkeys(ids))

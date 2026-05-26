from datetime import date
from decimal import Decimal

from app.schemas import (
    ANSWER_EVIDENCE_CONTEXT_VERSION,
    EvidencePackRead,
    EvidenceSpanRead,
    RetrievedChunkRead,
    RetrievedFinancialFactRead,
    RetrievalRequest,
    RetrievalResponse,
)
from app.services import build_answer_evidence_context, collect_answer_evidence_ids


def test_answer_evidence_context_exposes_stable_evidence_contract() -> None:
    request = RetrievalRequest(ticker="aapl", question="What was latest revenue?")
    response = make_response()

    context = build_answer_evidence_context(request, response)

    assert context.contract_version == ANSWER_EVIDENCE_CONTEXT_VERSION
    assert context.ticker == "AAPL"
    assert context.question == request.question
    assert context.final_evidence_pack.primary_financial_statement_chunks[0].evidence_id == "chunk:101"
    assert context.retrieved_facts[0].evidence_id == "financial_fact:501"
    assert context.allowed_evidence_ids == [
        "chunk:101",
        "span:101:primary_financial_statement_chunks:0:80",
        "financial_fact:501",
    ]
    assert "retrieval_trace" not in context.model_dump(mode="json")


def test_collect_answer_evidence_ids_dedupes_pack_and_fact_ids() -> None:
    response = make_response()
    response.retrieved_facts[0].evidence_id = "chunk:101"

    assert collect_answer_evidence_ids(response) == [
        "chunk:101",
        "span:101:primary_financial_statement_chunks:0:80",
    ]


def make_response() -> RetrievalResponse:
    chunk = RetrievedChunkRead(
        evidence_id="chunk:101",
        chunk_id=101,
        filing_id=10,
        section_id=20,
        score=0.42,
        fusion_score=0.05,
        source_ranks={"dense:slot": 1},
        rerank_boosts={"section_match": 0.15},
        snippet="Total net sales were $111.2 billion.",
        form_type="10-Q",
        filing_date=date(2026, 5, 1),
        section_label="PART I - ITEM 1 - Financial Statements",
        sec_url="https://www.sec.gov/Archives/example.htm",
        accession_number="0000320193-26-000013",
        start_page=5,
        end_page=6,
        has_table=True,
    )
    span = EvidenceSpanRead(
        evidence_id="span:101:primary_financial_statement_chunks:0:80",
        chunk_id=101,
        source_chunk_evidence_id="chunk:101",
        role="primary_financial_statement_chunks",
        score=0.91,
        support_kind="statement_value",
        text="Total net sales were $111.2 billion.",
        start_char=0,
        end_char=80,
        reasons=["strong_metric:revenue", "numeric_value"],
        form_type="10-Q",
        filing_date=date(2026, 5, 1),
        section_label="PART I - ITEM 1 - Financial Statements",
        sec_url="https://www.sec.gov/Archives/example.htm",
        accession_number="0000320193-26-000013",
        start_page=5,
        end_page=6,
    )
    fact = RetrievedFinancialFactRead(
        evidence_id="financial_fact:501",
        fact_id=501,
        score=0.18,
        canonical_metric_key="revenue",
        label="Revenue",
        period_start=date(2025, 12, 28),
        period_end=date(2026, 3, 28),
        duration_class="quarter",
        period_label="Q2 2026 quarter",
        source_fiscal_year=2026,
        fact_fiscal_year=2026,
        fiscal_period="Q2",
        form_type="10-Q",
        filed_date=date(2026, 5, 1),
        unit="USD",
        value=Decimal("111184000000"),
        source_accession_number="0000320193-26-000013",
        source_filing_id=10,
        source_filing_url="https://www.sec.gov/Archives/example.htm",
        source_fact_id="fact-501",
        is_computed=False,
        calculation_notes=None,
    )
    return RetrievalResponse(
        retrieval_plan={
            "question_type": "metric",
            "target_sections": ["Financial Statements"],
            "metric_keys": ["revenue"],
            "time_scope": "latest",
            "comparison_basis": "none",
            "comparison_candidates": [],
            "default_comparison_basis": None,
            "ambiguities": [],
            "forms": ["10-Q"],
            "preferred_forms": ["10-Q"],
            "dense_queries": ["latest revenue"],
            "dense_query_specs": [],
            "lexical_queries": ["revenue"],
            "rule_confidence": 0.8,
            "matched_rules": ["metric:revenue"],
            "planner_source": "rule_validated",
            "confidence_breakdown": {},
            "needs_financial_facts": True,
            "needs_text_chunks": True,
            "needs_metric_comparisons": False,
            "evidence_roles": ["primary_financial_statement_chunks"],
            "requires_llm_fallback_reason": None,
        },
        retrieved_chunks=[chunk],
        retrieved_facts=[fact],
        metric_comparisons=[],
        final_evidence_pack=EvidencePackRead(
            primary_financial_statement_chunks=[chunk],
            primary_financial_statement_spans=[span],
        ),
        source_coverage_summary={
            "chunk_count": 1,
            "fact_count": 1,
            "evidence_span_count": 1,
        },
        retrieval_trace={"candidate_counts": {"dense": 1}},
    )

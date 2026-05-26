from __future__ import annotations

from datetime import UTC, date, datetime

from app.evals.retrieval_dump import (
    RetrievalDump,
    format_retrieval_dump_markdown,
    highlighted_source_url,
)
from app.schemas.retrieval import (
    EvidencePackRead,
    RetrievedChunkRead,
    RetrievalResponse,
)


def make_chunk() -> RetrievedChunkRead:
    return RetrievedChunkRead(
        evidence_id="chunk:47",
        chunk_id=47,
        filing_id=9,
        section_id=3,
        score=0.31,
        fusion_score=0.04,
        source_ranks={"dense:slot": 1, "lexical": 2},
        rerank_boosts={"section_match": 0.15},
        snippet="Revenue increased because services improved.",
        form_type="10-Q",
        filing_date=date(2026, 5, 1),
        section_label="PART I - ITEM 2 - Management's Discussion and Analysis",
        sec_url="https://www.sec.gov/Archives/example.htm",
        accession_number="0000000000-26-000001",
        start_page=12,
        end_page=13,
        has_table=False,
    )


def make_response(chunk: RetrievedChunkRead) -> RetrievalResponse:
    return RetrievalResponse(
        retrieval_plan={
            "question_type": "mixed",
            "target_sections": ["Management's Discussion and Analysis"],
            "metric_keys": ["revenue"],
            "time_scope": "latest",
            "comparison_basis": "none",
            "comparison_candidates": [],
            "default_comparison_basis": None,
            "ambiguities": [],
            "forms": ["10-Q"],
            "preferred_forms": ["10-Q"],
            "dense_queries": ["latest revenue drivers"],
            "dense_query_specs": [
                {"role": "slot", "text": "latest revenue drivers", "weight": 1.0}
            ],
            "lexical_queries": ["revenue"],
            "rule_confidence": 0.86,
            "matched_rules": ["metric:revenue"],
            "planner_source": "rule_validated",
            "confidence_breakdown": {},
            "needs_financial_facts": True,
            "needs_text_chunks": True,
            "needs_metric_comparisons": False,
            "evidence_roles": ["mda_explanation_chunks"],
            "requires_llm_fallback_reason": None,
        },
        retrieved_chunks=[chunk],
        retrieved_facts=[],
        metric_comparisons=[],
        final_evidence_pack=EvidencePackRead(mda_explanation_chunks=[chunk]),
        source_coverage_summary={"chunk_count": 1, "fact_count": 0},
        retrieval_trace={
            "candidate_counts": {"dense": 1, "lexical": 1},
            "chunk_scope": {
                "latest_filing_date": "2026-05-01",
                "reason": "comparison_basis:latest_quarter_yoy",
            },
            "dense_query_sources": [
                {"source": "dense:slot", "candidate_count": 1, "weight": 1.0}
            ],
            "degraded": [],
        },
    )


def test_highlighted_source_url_uses_filing_and_chunk_ids() -> None:
    assert highlighted_source_url(make_chunk()) == "/filings/9/chunks/47/source"


def test_format_retrieval_dump_markdown_contains_judge_context_and_chunk_text() -> None:
    chunk = make_chunk()
    dump = RetrievalDump(
        ticker="AAPL",
        question="Why did revenue grow?",
        response=make_response(chunk),
        chunk_text_by_id={
            47: "Full MD&A text: revenue increased because services improved."
        },
        generated_at=datetime(2026, 5, 25, 12, 0, tzinfo=UTC),
    )

    markdown = format_retrieval_dump_markdown(dump)

    assert "Rate each chunk from 0 to 3" in markdown
    assert "/filings/9/chunks/47/source" in markdown
    assert "comparison_basis:latest_quarter_yoy" in markdown
    assert "Full MD&A text" in markdown
    assert "dense:slot" in markdown

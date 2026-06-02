from collections.abc import Generator

from fastapi.testclient import TestClient

import app.api.routes.research as research_route
from app.db import get_db_session
from app.main import app


class FakeSession:
    pass


class FakeRetrievalService:
    def __init__(self, db) -> None:
        self.db = db

    def retrieve(self, request):
        return {
            "retrieval_plan": {
                "question_type": "metric",
                "target_sections": [],
                "metric_keys": ["revenue"],
                "time_scope": "latest",
                "forms": [],
                "dense_queries": [request.question],
                "lexical_queries": ["revenue"],
                "matched_rules": ["metric:revenue"],
            },
            "retrieved_chunks": [],
            "retrieved_facts": [],
            "source_coverage_summary": {"chunk_count": 0, "fact_count": 0},
            "retrieval_trace": {"candidate_counts": {"dense": 0}},
        }


class FakePlanner:
    def plan(self, question, *, form_type=None, section=None):
        return FakePlan(
            {
                "question_type": "mixed",
                "target_sections": ["Management's Discussion and Analysis"],
                "metric_keys": ["revenue", "gross_margin"],
                "time_scope": "comparison_trend",
                "comparison_basis": "latest_quarter_yoy",
                "comparison_candidates": ["latest_quarter_yoy"],
                "default_comparison_basis": "latest_quarter_yoy",
                "ambiguities": [],
                "forms": [form_type] if form_type else ["10-Q"],
                "preferred_forms": [form_type] if form_type else ["10-Q"],
                "dense_queries": [question],
                "dense_query_specs": [
                    {"role": "mda", "text": question, "weight": 1.0},
                ],
                "lexical_queries": ["revenue growth", "gross margin"],
                "matched_rules": ["planner:llm", "validation:schema"],
                "planner_source": "llm_validated",
                "needs_financial_facts": True,
                "needs_text_chunks": True,
                "needs_metric_comparisons": True,
                "evidence_roles": [
                    "metric_comparisons",
                    "mda_explanation_chunks",
                ],
            }
        )


class FakePlan:
    def __init__(self, payload):
        self.payload = payload

    def to_dict(self):
        return self.payload


def override_db_session() -> None:
    def _override() -> Generator[FakeSession, None, None]:
        yield FakeSession()

    app.dependency_overrides[get_db_session] = _override


def test_plan_endpoint_returns_llm_planner_slots(monkeypatch) -> None:
    monkeypatch.setattr(research_route, "QueryPlanner", FakePlanner)
    client = TestClient(app)

    response = client.post(
        "/research/plan",
        json={
            "question": "苹果最近一个季度收入增长和毛利率变化原因是什么？",
            "form_type": "10-Q",
        },
    )

    body = response.json()
    assert response.status_code == 200
    assert body["planner_source"] == "llm_validated"
    assert body["question_type"] == "mixed"
    assert body["metric_keys"] == ["revenue", "gross_margin"]
    assert body["target_sections"] == ["Management's Discussion and Analysis"]
    assert body["needs_metric_comparisons"]


def test_retrieve_endpoint_returns_retrieval_trace(monkeypatch) -> None:
    monkeypatch.setattr(research_route, "RetrievalService", FakeRetrievalService)
    override_db_session()
    client = TestClient(app)

    response = client.post(
        "/research/retrieve",
        json={"ticker": "AAPL", "question": "What was latest revenue?"},
    )

    app.dependency_overrides.clear()
    assert response.status_code == 200
    assert response.json()["retrieval_plan"]["metric_keys"] == ["revenue"]
    assert response.json()["retrieval_trace"]["candidate_counts"]["dense"] == 0


def test_query_endpoint_returns_cited_answer(monkeypatch) -> None:
    class FakeResearchAnswerService:
        def __init__(self, db) -> None:
            self.db = db

        def answer(self, request):
            return {
                "answer": f"{request.ticker} revenue was supported by filings. [financial_fact:501]",
                "citations": [
                    {
                        "evidence_id": "financial_fact:501",
                        "evidence_type": "financial_fact",
                        "source_label": "Revenue",
                        "text": "Revenue was $111.2B.",
                        "sec_url": "https://www.sec.gov/Archives/example.htm",
                        "form_type": "10-Q",
                        "filing_date": "2026-05-01",
                        "section": None,
                        "pages": None,
                        "source_ids": {"fact_id": 501},
                    }
                ],
                "retrieved_evidence_ids": ["financial_fact:501"],
                "prompt_evidence_ids": ["financial_fact:501"],
                "validation_status": "passed",
                "validation": {
                    "status": "passed",
                    "cited_evidence_ids": ["financial_fact:501"],
                    "allowed_evidence_ids": ["financial_fact:501"],
                    "prompt_evidence_ids": ["financial_fact:501"],
                    "errors": [],
                },
                "limitations": [],
                "source_coverage_summary": {"fact_count": 1},
                "retrieval_plan": {
                    "question_type": "metric",
                    "target_sections": [],
                    "metric_keys": ["revenue"],
                    "time_scope": "latest",
                    "forms": ["10-Q"],
                    "dense_queries": [request.question],
                    "lexical_queries": ["revenue"],
                    "matched_rules": ["metric:revenue"],
                },
                "final_evidence_pack": {},
            }

    monkeypatch.setattr(research_route, "ResearchAnswerService", FakeResearchAnswerService)
    override_db_session()
    client = TestClient(app)

    response = client.post(
        "/research/query",
        json={"ticker": "AAPL", "question": "What was latest revenue?"},
    )

    app.dependency_overrides.clear()
    body = response.json()
    assert response.status_code == 200
    assert body["validation_status"] == "passed"
    assert body["citations"][0]["evidence_id"] == "financial_fact:501"
    assert body["retrieval_plan"]["metric_keys"] == ["revenue"]


def test_retrieve_endpoint_supports_compact_analysis_view(monkeypatch) -> None:
    class FakeAnalysisRetrievalService:
        def __init__(self, db) -> None:
            self.db = db

        def retrieve(self, request):
            return {
                "retrieval_plan": {
                    "question_type": "risk",
                    "target_sections": ["Risk Factors"],
                    "metric_keys": [],
                    "time_scope": "latest",
                    "forms": [],
                    "dense_queries": [request.question],
                    "lexical_queries": ["risk factors"],
                    "matched_rules": ["section:risk_factors", "time:latest"],
                },
                "retrieved_chunks": [
                    {
                        "evidence_id": "chunk:47",
                        "type": "chunk",
                        "chunk_id": 47,
                        "filing_id": 1,
                        "section_id": 6,
                        "score": 0.32,
                        "fusion_score": 0.03,
                        "source_ranks": {"dense": 1, "lexical": 2},
                        "rerank_boosts": {"section_match": 0.15},
                        "snippet": "Risk factor text " * 40,
                        "form_type": "10-Q",
                        "filing_date": "2026-05-01",
                        "section_label": "PART II - ITEM 1A - Risk Factors",
                        "sec_url": "https://www.sec.gov/Archives/aapl.htm",
                        "accession_number": "0000320193-26-000013",
                        "start_page": 23,
                        "end_page": 24,
                        "has_table": False,
                    }
                ],
                "retrieved_facts": [],
                "source_coverage_summary": {"chunk_count": 1, "fact_count": 0},
                "retrieval_trace": {
                    "candidate_counts": {"dense": 40, "lexical": 40},
                    "timing_ms": {"total_ms": 1000.0},
                    "degraded": [],
                    "retrieval_config": {"vector_search_mode": "exact"},
                    "fusion": {"47": {"fusion_score": 0.03}},
                },
            }

    monkeypatch.setattr(research_route, "RetrievalService", FakeAnalysisRetrievalService)
    override_db_session()
    client = TestClient(app)

    response = client.post(
        "/research/retrieve?view=analysis",
        json={"ticker": "AAPL", "question": "What are Apple latest risk factors?"},
    )

    app.dependency_overrides.clear()
    body = response.json()
    assert response.status_code == 200
    assert "retrieval_trace" not in body
    assert body["analysis_trace"]["candidate_counts"] == {"dense": 40, "lexical": 40}
    assert body["top_chunks"][0]["evidence_id"] == "chunk:47"
    assert len(body["top_chunks"][0]["snippet"]) < 280

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

from datetime import UTC, datetime

from decimal import Decimal

from app.models import DocumentChunk, FinancialFact
from app.schemas import EvidencePackRead, RetrievalRequest
from app.services import (
    build_final_evidence_pack,
    build_metric_comparisons,
    metadata_boosts,
    metric_text_boosts,
    weighted_rrf,
)
from app.services.retrieval import (
    Candidate,
    aggregate_source_candidates,
    build_chunk_filter_sql,
    build_retrieved_chunk,
    build_retrieved_fact,
    effective_dense_query_specs,
    evidence_candidate_limit,
    evidence_pack_comparison_limit,
    effective_form_types,
    form_priority_boost,
    latest_filing_scope_reason,
    make_snippet,
    select_evidence_spans_for_chunk,
    should_warn_empty_evidence_pack,
    weighted_rrf_sources,
)
from app.services.query_planner import RetrievalPlan

NOW = datetime(2026, 5, 20, 12, 0, tzinfo=UTC)


def make_plan() -> RetrievalPlan:
    return RetrievalPlan(
        question_type="risk",
        target_sections=["Risk Factors"],
        metric_keys=[],
        time_scope="latest",
        comparison_basis="none",
        comparison_candidates=[],
        default_comparison_basis=None,
        ambiguities=[],
        forms=[],
        dense_queries=["risk factors"],
        lexical_queries=["risk factors"],
        matched_rules=["section:risk_factors", "time:latest"],
    )


def make_chunk(
    *,
    chunk_id: int,
    section: str,
    form_type: str = "10-K",
    text: str = "Risk factor text.",
    has_table: bool = False,
) -> DocumentChunk:
    return DocumentChunk(
        id=chunk_id,
        filing_id=20,
        section_id=30,
        chunk_index=0,
        chunk_text=text,
        token_count=3,
        accession_number="0000320193-24-000123",
        form_type=form_type,
        filing_date=datetime(2024, 11, 1, tzinfo=UTC).date(),
        section_label=section,
        sec_url="https://www.sec.gov/Archives/aapl.htm",
        start_page=1,
        end_page=1,
        start_display_page=None,
        end_display_page=None,
        element_ids=[],
        xbrl_tags=[],
        source_start_offset=None,
        source_end_offset=None,
        has_table=has_table,
        created_at=NOW,
        updated_at=NOW,
    )


def make_metric_plan() -> RetrievalPlan:
    return RetrievalPlan(
        question_type="trend",
        target_sections=["Financial Statements"],
        metric_keys=["revenue"],
        time_scope="comparison_trend",
        comparison_basis="ambiguous",
        comparison_candidates=["latest_quarter_yoy", "latest_ytd_yoy", "latest_fy_yoy"],
        default_comparison_basis="latest_quarter_yoy",
        ambiguities=["Question does not specify quarterly, year-to-date, annual, or multi-year growth."],
        forms=[],
        dense_queries=["revenue growth"],
        lexical_queries=['"total net sales"'],
        matched_rules=["metric:revenue", "time:comparison_trend"],
    )


def make_broad_comparison_plan() -> RetrievalPlan:
    return RetrievalPlan(
        question_type="broad_comparison",
        target_sections=[
            "Financial Statements",
            "Management's Discussion and Analysis",
        ],
        metric_keys=[
            "revenue",
            "gross_margin",
            "operating_income",
            "net_income",
        ],
        time_scope="comparison_trend",
        comparison_basis="latest_fy_yoy",
        comparison_candidates=["latest_fy_yoy"],
        default_comparison_basis="latest_fy_yoy",
        ambiguities=[],
        forms=["10-K"],
        dense_queries=["year-over-year financial performance changes"],
        lexical_queries=['"products and services performance"'],
        matched_rules=[
            "metric:company_change_default",
            "comparison_basis:latest_fy_yoy",
            "section:mda_company_change",
        ],
    )


def make_fact(
    *,
    fact_id: int,
    period_start: tuple[int, int, int],
    period_end: tuple[int, int, int],
    fiscal_period: str,
    value: str,
    source_fiscal_year: int | None = None,
    fact_fiscal_year: int | None = None,
) -> FinancialFact:
    default_fact_fiscal_year = period_end[0]
    return FinancialFact(
        id=fact_id,
        company_id=1,
        canonical_metric_key="revenue",
        taxonomy_tag="us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax",
        label="Revenue",
        period_start=datetime(*period_start, tzinfo=UTC).date(),
        period_end=datetime(*period_end, tzinfo=UTC).date(),
        source_fiscal_year=(
            source_fiscal_year if source_fiscal_year is not None else default_fact_fiscal_year
        ),
        fact_fiscal_year=(
            fact_fiscal_year if fact_fiscal_year is not None else default_fact_fiscal_year
        ),
        fiscal_period=fiscal_period,
        form_type="10-Q" if fiscal_period != "FY" else "10-K",
        filed_date=datetime(period_end[0], min(period_end[1] + 1, 12), 1, tzinfo=UTC).date(),
        unit="USD",
        value=Decimal(value),
        source_accession_number=f"accession-{fact_id}",
        source_filing_id=fact_id,
        source_filing_url=f"https://www.sec.gov/{fact_id}",
        source_fact_id=f"fact-{fact_id}",
        is_computed=False,
        calculation_notes=None,
        created_at=NOW,
        updated_at=NOW,
    )


def make_chunk_read(chunk: DocumentChunk, *, score: float):
    return build_retrieved_chunk(
        chunk,
        Candidate(chunk_id=chunk.id, fusion_score=score),
        {},
        metric_keys=["revenue"],
    )


def test_weighted_rrf_fuses_dense_and_lexical_candidates() -> None:
    fused = weighted_rrf(
        dense_candidates=[(1, 0.1), (2, 0.2)],
        lexical_candidates=[(2, 0.9), (3, 0.8)],
    )

    assert fused[0].chunk_id == 2
    assert fused[0].source_ranks == {"dense": 2, "lexical": 1}
    assert {candidate.chunk_id for candidate in fused} == {1, 2, 3}


def test_effective_dense_query_specs_prefers_planner_specs() -> None:
    plan = RetrievalPlan(
        **{
            **make_metric_plan().to_dict(),
            "dense_queries": ["fallback query"],
            "dense_query_specs": [
                {"role": "MDA Drivers", "text": " revenue drivers ", "weight": 0.85},
                {"role": "MDA Drivers", "text": "margin drivers", "weight": "0.7"},
                {"role": "Original", "text": "revenue drivers", "weight": 0.4},
            ],
        }
    )

    specs = effective_dense_query_specs(plan)

    assert [(spec.source_name, spec.text, spec.weight) for spec in specs] == [
        ("dense:mda_drivers", "revenue drivers", 0.85),
        ("dense:mda_drivers_2", "margin drivers", 0.7),
    ]


def test_weighted_rrf_sources_combines_multiple_dense_queries() -> None:
    fused = weighted_rrf_sources(
        [
            ("dense:slot", [(1, 0.1), (2, 0.2)], 1.0),
            ("dense:original", [(2, 0.1), (3, 0.2)], 0.4),
            ("lexical", [(3, 0.9)], 0.9),
        ]
    )

    assert fused[0].chunk_id == 2
    assert fused[0].source_ranks == {"dense:slot": 2, "dense:original": 1}
    assert fused[1].chunk_id == 3
    assert fused[1].source_ranks == {"dense:original": 2, "lexical": 1}


def test_aggregate_source_candidates_exposes_dense_fused_ranking() -> None:
    aggregate = aggregate_source_candidates(
        [
            ("dense:slot", [(1, 0.1), (2, 0.2)], 1.0),
            ("dense:original", [(2, 0.1), (3, 0.2)], 0.4),
        ],
        limit=2,
    )

    assert [chunk_id for chunk_id, _ in aggregate] == [2, 1]


def test_metadata_boosts_reward_section_latest_and_form_priority() -> None:
    chunk = make_chunk(chunk_id=1, section="PART I - ITEM 1A - Risk Factors")

    boosts = metadata_boosts(chunk, plan=make_plan(), latest_date=chunk.filing_date)

    assert boosts["section_match"] == 0.15
    assert boosts["latest_filing"] == 0.10
    assert boosts["form_priority"] == 0.05


def test_latest_quarter_basis_boosts_latest_10q_over_10k() -> None:
    plan = make_metric_plan()
    plan = RetrievalPlan(
        **{
            **plan.to_dict(),
            "time_scope": "latest",
            "comparison_basis": "latest_quarter_yoy",
            "comparison_candidates": ["latest_quarter_yoy"],
            "default_comparison_basis": "latest_quarter_yoy",
        }
    )
    ten_q_chunk = make_chunk(chunk_id=10, section="PART I - ITEM 2 - MD&A", form_type="10-Q")
    ten_k_chunk = make_chunk(chunk_id=11, section="PART II - ITEM 8 - Financial Statements", form_type="10-K")

    assert form_priority_boost(ten_q_chunk, plan) > form_priority_boost(ten_k_chunk, plan)


def test_metric_text_boosts_reward_strong_revenue_statement_context() -> None:
    chunk = make_chunk(
        chunk_id=2,
        section="PART I - ITEM 1 - Financial Statements",
    )
    chunk.chunk_text = (
        "CONDENSED CONSOLIDATED STATEMENTS OF OPERATIONS "
        "Three Months Ended Net sales increased to $111 billion."
    )

    boosts = metadata_boosts(chunk, plan=make_metric_plan(), latest_date=chunk.filing_date)

    assert boosts["section_match"] == 0.15
    assert boosts["strong_metric_match"] == 0.08
    assert boosts["statement_context_match"] == 0.06
    assert "weak_metric_match" not in boosts
    assert "negative_metric_context" not in boosts


def test_metric_text_boosts_reward_non_revenue_metric_profiles() -> None:
    chunk = make_chunk(
        chunk_id=22,
        section="PART I - ITEM 1 - Financial Statements",
    )
    chunk.chunk_text = (
        "CONSOLIDATED STATEMENTS OF OPERATIONS "
        "Net income increased as services gross margin improved."
    )

    boosts = metric_text_boosts(chunk, ["net_income"])

    assert boosts["strong_metric_match"] == 0.08
    assert boosts["statement_context_match"] == 0.06
    assert "weak_metric_match" not in boosts


def test_metric_text_boosts_do_not_reward_statement_context_without_metric_terms() -> None:
    chunk = make_chunk(
        chunk_id=23,
        section="PART I - ITEM 1 - Financial Statements",
    )
    chunk.chunk_text = "CONDENSED CONSOLIDATED STATEMENTS OF OPERATIONS"

    boosts = metric_text_boosts(chunk, ["net_income"])

    assert boosts == {}


def test_metric_text_boosts_penalize_deferred_revenue_context() -> None:
    chunk = make_chunk(
        chunk_id=3,
        section="PART II - ITEM 8 - Financial Statements and Supplementary Data",
    )
    chunk.chunk_text = "As of March 28, 2026, deferred revenue was $13.7 billion."

    boosts = metric_text_boosts(chunk, ["revenue"])

    assert boosts["weak_metric_match"] == 0.01
    assert boosts["negative_metric_context"] == -0.07


def test_broad_comparison_boosts_mda_change_explanations() -> None:
    chunk = make_chunk(
        chunk_id=4,
        section="PART II - ITEM 7 - Management’s Discussion and Analysis",
        text=(
            "Products and Services Performance The following table shows net sales by "
            "category. iPhone net sales increased compared to 2024 primarily due to "
            "higher net sales of Pro models. Gross margin percentage increased "
            "primarily due to a different mix."
        ),
        has_table=True,
    )

    boosts = metadata_boosts(
        chunk,
        plan=make_broad_comparison_plan(),
        latest_date=chunk.filing_date,
    )

    assert boosts["section_match"] == 0.15
    assert boosts["mda_change_explanation"] == 0.14
    assert boosts["business_breakdown_context"] == 0.08
    assert boosts["margin_profit_driver_context"] == 0.08


def test_broad_comparison_penalizes_accounting_controls_noise() -> None:
    chunk = make_chunk(
        chunk_id=5,
        section="PART II - ITEM 9A - Controls and Procedures",
        text=(
            "A control system can provide only reasonable assurance that objectives "
            "of the control system are met."
        ),
    )

    boosts = metadata_boosts(
        chunk,
        plan=make_broad_comparison_plan(),
        latest_date=chunk.filing_date,
    )

    assert boosts["broad_change_noise"] == -0.16


def test_chunk_filter_uses_planner_forms_when_request_form_absent() -> None:
    plan = RetrievalPlan(
        **{
            **make_metric_plan().to_dict(),
            "forms": ["10-Q"],
        }
    )
    request = RetrievalRequest(ticker="AAPL", question="How did Apple do last quarter?")
    params: dict[str, object] = {}

    sql = build_chunk_filter_sql(request, params, plan=plan)

    assert "AND dc.form_type = :filter_form_type" in sql
    assert params["filter_form_type"] == "10-Q"


def test_chunk_filter_request_form_overrides_planner_forms() -> None:
    plan = RetrievalPlan(
        **{
            **make_metric_plan().to_dict(),
            "forms": ["10-Q"],
        }
    )
    request = RetrievalRequest(
        ticker="AAPL",
        question="Show annual revenue.",
        form_type="10-K",
    )
    params: dict[str, object] = {}

    sql = build_chunk_filter_sql(request, params, plan=plan)

    assert effective_form_types(request, plan) == ["10-K"]
    assert "AND dc.form_type = :filter_form_type" in sql
    assert params["filter_form_type"] == "10-K"


def test_chunk_filter_can_pin_latest_filing_date() -> None:
    plan = RetrievalPlan(
        **{
            **make_metric_plan().to_dict(),
            "forms": ["10-Q"],
        }
    )
    latest_filing_date = datetime(2026, 5, 1, tzinfo=UTC).date()
    request = RetrievalRequest(ticker="AAPL", question="Why did revenue grow last quarter?")
    params: dict[str, object] = {}

    sql = build_chunk_filter_sql(
        request,
        params,
        plan=plan,
        latest_filing_date=latest_filing_date,
    )

    assert "AND dc.form_type = :filter_form_type" in sql
    assert "AND dc.filing_date = :latest_filing_date" in sql
    assert params["latest_filing_date"] == latest_filing_date


def test_latest_filing_scope_applies_to_explicit_latest_quarter_basis() -> None:
    plan = RetrievalPlan(
        **{
            **make_metric_plan().to_dict(),
            "comparison_basis": "latest_quarter_yoy",
            "comparison_candidates": ["latest_quarter_yoy"],
        }
    )

    assert latest_filing_scope_reason(plan) == "comparison_basis:latest_quarter_yoy"


def test_latest_filing_scope_does_not_apply_to_ambiguous_trend() -> None:
    assert latest_filing_scope_reason(make_metric_plan()) is None


def test_empty_metric_evidence_pack_is_warning_condition() -> None:
    empty_pack = EvidencePackRead(
        metric_comparisons=[],
        primary_financial_statement_chunks=[],
        mda_explanation_chunks=[],
        segment_or_product_breakdown_chunks=[],
        risk_factor_chunks=[],
        annual_context_chunks=[],
    )

    assert should_warn_empty_evidence_pack(make_metric_plan(), empty_pack)
    assert should_warn_empty_evidence_pack(make_plan(), empty_pack)


def test_build_metric_comparisons_returns_quarter_ytd_and_fy_pairs_for_ambiguous_growth() -> None:
    facts = [
        make_fact(
            fact_id=1,
            period_start=(2026, 1, 1),
            period_end=(2026, 3, 28),
            fiscal_period="Q2",
            value="111184000000",
        ),
        make_fact(
            fact_id=2,
            period_start=(2025, 1, 1),
            period_end=(2025, 3, 29),
            fiscal_period="Q2",
            value="95359000000",
        ),
        make_fact(
            fact_id=3,
            period_start=(2025, 9, 28),
            period_end=(2026, 3, 28),
            fiscal_period="Q2",
            value="254940000000",
        ),
        make_fact(
            fact_id=4,
            period_start=(2024, 9, 29),
            period_end=(2025, 3, 29),
            fiscal_period="Q2",
            value="219659000000",
        ),
        make_fact(
            fact_id=5,
            period_start=(2024, 9, 29),
            period_end=(2025, 9, 27),
            fiscal_period="FY",
            value="416161000000",
        ),
        make_fact(
            fact_id=6,
            period_start=(2023, 10, 1),
            period_end=(2024, 9, 28),
            fiscal_period="FY",
            value="391035000000",
        ),
    ]

    comparisons = build_metric_comparisons(facts, make_metric_plan())

    assert [comparison.basis for comparison in comparisons] == [
        "latest_quarter_yoy",
        "latest_ytd_yoy",
        "latest_fy_yoy",
    ]
    assert comparisons[0].current_fact_id == 1
    assert comparisons[0].prior_fact_id == 2
    assert comparisons[1].current_fact_id == 3
    assert comparisons[1].prior_fact_id == 4
    assert comparisons[2].current_fact_id == 5
    assert comparisons[2].prior_fact_id == 6


def test_metric_comparisons_run_for_latest_time_scope_with_default_basis() -> None:
    facts = [
        make_fact(
            fact_id=1,
            period_start=(2026, 1, 1),
            period_end=(2026, 3, 28),
            fiscal_period="Q2",
            value="111184000000",
        ),
        make_fact(
            fact_id=2,
            period_start=(2025, 1, 1),
            period_end=(2025, 3, 29),
            fiscal_period="Q2",
            value="95359000000",
        ),
    ]
    plan = RetrievalPlan(
        **{
            **make_metric_plan().to_dict(),
            "time_scope": "latest",
            "comparison_basis": "latest_quarter_yoy",
            "comparison_candidates": ["latest_quarter_yoy"],
            "default_comparison_basis": "latest_quarter_yoy",
        }
    )

    comparisons = build_metric_comparisons(facts, plan)

    assert len(comparisons) == 1
    assert comparisons[0].current_fact_id == 1
    assert comparisons[0].prior_fact_id == 2
    assert comparisons[0].current_duration_class == "quarter"
    assert Decimal("0.1659") < comparisons[0].growth_rate < Decimal("0.1660")


def test_metric_comparisons_include_previous_quarter_yoy_for_acceleration() -> None:
    facts = [
        make_fact(
            fact_id=1,
            period_start=(2025, 12, 28),
            period_end=(2026, 3, 28),
            fiscal_period="Q2",
            value="111184000000",
            fact_fiscal_year=2026,
        ),
        make_fact(
            fact_id=2,
            period_start=(2024, 12, 29),
            period_end=(2025, 3, 29),
            fiscal_period="Q2",
            value="95359000000",
            fact_fiscal_year=2025,
        ),
        make_fact(
            fact_id=3,
            period_start=(2025, 9, 28),
            period_end=(2025, 12, 27),
            fiscal_period="Q1",
            value="143756000000",
            fact_fiscal_year=2026,
        ),
        make_fact(
            fact_id=4,
            period_start=(2024, 9, 29),
            period_end=(2024, 12, 28),
            fiscal_period="Q1",
            value="124300000000",
            fact_fiscal_year=2025,
        ),
    ]
    plan = RetrievalPlan(
        **{
            **make_metric_plan().to_dict(),
            "question_type": "growth_acceleration",
            "comparison_basis": "latest_quarter_yoy",
            "comparison_candidates": ["latest_quarter_yoy", "previous_quarter_yoy"],
            "default_comparison_basis": "latest_quarter_yoy",
        }
    )

    comparisons = build_metric_comparisons(facts, plan)

    assert [comparison.basis for comparison in comparisons] == [
        "latest_quarter_yoy",
        "previous_quarter_yoy",
    ]
    assert comparisons[0].current_fact_id == 1
    assert comparisons[0].prior_fact_id == 2
    assert comparisons[1].current_fact_id == 3
    assert comparisons[1].prior_fact_id == 4
    assert Decimal("0.1659") < comparisons[0].growth_rate < Decimal("0.1660")
    assert Decimal("0.1565") < comparisons[1].growth_rate < Decimal("0.1566")


def test_metric_comparisons_include_previous_fy_yoy_for_annual_acceleration() -> None:
    facts = [
        make_fact(
            fact_id=10,
            period_start=(2024, 9, 29),
            period_end=(2025, 9, 27),
            fiscal_period="FY",
            value="416161000000",
            fact_fiscal_year=2025,
        ),
        make_fact(
            fact_id=11,
            period_start=(2023, 10, 1),
            period_end=(2024, 9, 28),
            fiscal_period="FY",
            value="391035000000",
            fact_fiscal_year=2024,
        ),
        make_fact(
            fact_id=12,
            period_start=(2022, 9, 25),
            period_end=(2023, 9, 30),
            fiscal_period="FY",
            value="383285000000",
            fact_fiscal_year=2023,
        ),
    ]
    plan = RetrievalPlan(
        **{
            **make_metric_plan().to_dict(),
            "question_type": "growth_acceleration",
            "comparison_basis": "latest_fy_yoy",
            "comparison_candidates": ["latest_fy_yoy", "previous_fy_yoy"],
            "default_comparison_basis": "latest_fy_yoy",
        }
    )

    comparisons = build_metric_comparisons(facts, plan)

    assert [comparison.basis for comparison in comparisons] == [
        "latest_fy_yoy",
        "previous_fy_yoy",
    ]
    assert comparisons[0].current_fact_id == 10
    assert comparisons[0].prior_fact_id == 11
    assert comparisons[1].current_fact_id == 11
    assert comparisons[1].prior_fact_id == 12


def test_evidence_pack_comparison_limit_scales_for_multi_metric_growth() -> None:
    plan = RetrievalPlan(
        **{
            **make_metric_plan().to_dict(),
            "metric_keys": ["revenue", "operating_income", "net_income"],
        }
    )

    assert evidence_pack_comparison_limit(plan) == 6


def test_evidence_pack_comparison_limit_keeps_multi_metric_single_basis() -> None:
    plan = RetrievalPlan(
        **{
            **make_metric_plan().to_dict(),
            "question_type": "broad_comparison",
            "metric_keys": [
                "revenue",
                "gross_margin",
                "operating_income",
                "net_income",
            ],
            "comparison_basis": "latest_fy_yoy",
            "comparison_candidates": ["latest_fy_yoy"],
            "default_comparison_basis": "latest_fy_yoy",
        }
    )

    assert evidence_pack_comparison_limit(plan) == 4


def test_evidence_candidate_limit_expands_for_broad_comparison() -> None:
    assert evidence_candidate_limit(make_broad_comparison_plan(), top_k=10) == 30


def test_revenue_comparison_uses_quarter_fact_not_ytd_fact() -> None:
    facts = [
        make_fact(
            fact_id=1,
            period_start=(2026, 1, 1),
            period_end=(2026, 3, 28),
            fiscal_period="Q2",
            value="111184000000",
        ),
        make_fact(
            fact_id=2,
            period_start=(2025, 9, 28),
            period_end=(2026, 3, 28),
            fiscal_period="Q2",
            value="254940000000",
        ),
        make_fact(
            fact_id=3,
            period_start=(2025, 1, 1),
            period_end=(2025, 3, 29),
            fiscal_period="Q2",
            value="95359000000",
        ),
    ]
    plan = RetrievalPlan(
        **{
            **make_metric_plan().to_dict(),
            "comparison_basis": "latest_quarter_yoy",
            "comparison_candidates": ["latest_quarter_yoy"],
            "default_comparison_basis": "latest_quarter_yoy",
        }
    )

    comparisons = build_metric_comparisons(facts, plan)

    assert comparisons[0].current_fact_id == 1
    assert comparisons[0].prior_fact_id == 3


def test_fact_evidence_exposes_duration_class_and_period_label() -> None:
    fact = make_fact(
        fact_id=7,
        period_start=(2026, 1, 1),
        period_end=(2026, 3, 28),
        fiscal_period="Q2",
        value="111184000000",
    )

    evidence = build_retrieved_fact(fact, rank=1)

    assert evidence.duration_class == "quarter"
    assert evidence.period_label == "Q2 2026 quarter"


def test_fact_period_label_uses_fact_fiscal_year_not_source_filing_year() -> None:
    quarter_fact = make_fact(
        fact_id=8,
        period_start=(2024, 12, 29),
        period_end=(2025, 3, 29),
        fiscal_period="Q2",
        value="95359000000",
        source_fiscal_year=2026,
        fact_fiscal_year=2025,
    )
    ytd_fact = make_fact(
        fact_id=9,
        period_start=(2024, 9, 29),
        period_end=(2025, 3, 29),
        fiscal_period="Q2",
        value="219659000000",
        source_fiscal_year=2026,
        fact_fiscal_year=2025,
    )
    fy_fact = make_fact(
        fact_id=10,
        period_start=(2023, 10, 1),
        period_end=(2024, 9, 28),
        fiscal_period="FY",
        value="391035000000",
        source_fiscal_year=2025,
        fact_fiscal_year=2024,
    )

    assert build_retrieved_fact(quarter_fact, rank=1).period_label == "Q2 2025 quarter"
    assert build_retrieved_fact(ytd_fact, rank=1).period_label == "Q2 2025 year-to-date"
    assert build_retrieved_fact(fy_fact, rank=1).period_label == "FY 2024"


def test_metric_comparison_labels_use_fact_fiscal_years() -> None:
    current = make_fact(
        fact_id=11,
        period_start=(2025, 12, 28),
        period_end=(2026, 3, 28),
        fiscal_period="Q2",
        value="111184000000",
        source_fiscal_year=2026,
        fact_fiscal_year=2026,
    )
    prior = make_fact(
        fact_id=12,
        period_start=(2024, 12, 29),
        period_end=(2025, 3, 29),
        fiscal_period="Q2",
        value="95359000000",
        source_fiscal_year=2026,
        fact_fiscal_year=2025,
    )
    plan = RetrievalPlan(
        **{
            **make_metric_plan().to_dict(),
            "comparison_basis": "latest_quarter_yoy",
            "comparison_candidates": ["latest_quarter_yoy"],
            "default_comparison_basis": "latest_quarter_yoy",
        }
    )

    comparison = build_metric_comparisons([current, prior], plan)[0]

    assert comparison.current_period_label == "Q2 2026 quarter"
    assert comparison.prior_period_label == "Q2 2025 quarter"
    assert comparison.current_fact_fiscal_year == 2026
    assert comparison.prior_source_fiscal_year == 2026
    assert comparison.prior_fact_fiscal_year == 2025


def test_final_evidence_pack_uses_role_quotas_for_metric_performance() -> None:
    plan = RetrievalPlan(
        **{
            **make_metric_plan().to_dict(),
            "time_scope": "latest",
            "comparison_basis": "latest_quarter_yoy",
            "comparison_candidates": ["latest_quarter_yoy"],
            "default_comparison_basis": "latest_quarter_yoy",
        }
    )
    statement_chunk = make_chunk(
        chunk_id=101,
        section="PART I - ITEM 1 - Financial Statements",
        form_type="10-Q",
        text=(
            "CONDENSED CONSOLIDATED STATEMENTS OF OPERATIONS "
            "Three Months Ended Total net sales $111,184 $95,359."
        ),
        has_table=True,
    )
    segment_note_chunk = make_chunk(
        chunk_id=102,
        section="PART I - ITEM 1 - Financial Statements",
        form_type="10-Q",
        text=(
            "Note 10 Segment Information Net sales Americas Europe "
            "Greater China Japan Rest of Asia Pacific."
        ),
        has_table=True,
    )
    mda_explanation_chunk = make_chunk(
        chunk_id=103,
        section="PART I - ITEM 2 - Management’s Discussion and Analysis",
        form_type="10-Q",
        text=(
            "Products and services net sales increased during the second quarter "
            "compared to 2025 primarily due to higher net sales of iPhone and Services."
        ),
    )
    mda_segment_chunk = make_chunk(
        chunk_id=104,
        section="PART I - ITEM 2 - Management’s Discussion and Analysis",
        form_type="10-Q",
        text=(
            "Segment Operating Performance table shows net sales by reportable segment "
            "for Americas Europe and Greater China."
        ),
        has_table=True,
    )
    annual_chunk = make_chunk(
        chunk_id=105,
        section="PART II - ITEM 8 - Financial Statements and Supplementary Data",
        form_type="10-K",
        text="CONSOLIDATED STATEMENTS OF OPERATIONS Years ended Total net sales.",
        has_table=True,
    )
    current = make_fact(
        fact_id=20,
        period_start=(2025, 12, 28),
        period_end=(2026, 3, 28),
        fiscal_period="Q2",
        value="111184000000",
    )
    prior = make_fact(
        fact_id=21,
        period_start=(2024, 12, 29),
        period_end=(2025, 3, 29),
        fiscal_period="Q2",
        value="95359000000",
    )

    comparisons = build_metric_comparisons([current, prior], plan)
    pack, trace = build_final_evidence_pack(
        [
            make_chunk_read(segment_note_chunk, score=0.52),
            make_chunk_read(statement_chunk, score=0.50),
            make_chunk_read(mda_segment_chunk, score=0.39),
            make_chunk_read(mda_explanation_chunk, score=0.37),
            make_chunk_read(annual_chunk, score=0.36),
        ],
        comparisons,
        plan,
    )

    assert [item.evidence_id for item in pack.metric_comparisons] == [
        "metric_comparison:revenue:latest_quarter_yoy:20:21"
    ]
    assert [chunk.chunk_id for chunk in pack.primary_financial_statement_chunks] == [101]
    assert [chunk.chunk_id for chunk in pack.mda_explanation_chunks] == [103, 104]
    assert [chunk.chunk_id for chunk in pack.segment_or_product_breakdown_chunks] == [102, 104]
    assert pack.annual_context_chunks == []
    assert [span.chunk_id for span in pack.primary_financial_statement_spans] == [101]
    assert pack.primary_financial_statement_spans[0].support_kind == "statement_value"
    assert [span.chunk_id for span in pack.mda_explanation_spans] == [103, 104]
    assert pack.mda_explanation_spans[0].support_kind == "metric_driver"
    assert [span.chunk_id for span in pack.segment_or_product_breakdown_spans] == [102, 104]
    assert trace["chunk_quotas"]["annual_context_chunks"] == 0
    assert trace["selected_spans"]["mda_explanation_chunks"]


def test_final_evidence_pack_includes_risk_chunks_without_metrics() -> None:
    risk_chunk = make_chunk(
        chunk_id=130,
        section="PART I - ITEM 1A - Risk Factors",
        text=(
            "The Company faces intense competition and macroeconomic conditions "
            "could adversely affect its business, results of operations, and financial condition."
        ),
    )

    pack, trace = build_final_evidence_pack(
        [make_chunk_read(risk_chunk, score=0.52)],
        [],
        make_plan(),
    )

    assert [chunk.chunk_id for chunk in pack.risk_factor_chunks] == [130]
    assert [span.chunk_id for span in pack.risk_factor_spans] == [130]
    assert pack.risk_factor_spans[0].support_kind == "risk_factor"
    assert "risk_factor_chunks" in trace["candidate_roles"]
    assert trace["selected"]["risk_factor_chunks"] == ["chunk:130"]


def test_final_evidence_pack_detects_risk_chunks_from_text_when_label_is_noisy() -> None:
    risk_chunk = make_chunk(
        chunk_id=132,
        section="PART I - ITEM 2 - Other Information",
        text=(
            "Risk Factors The Company's international operations are subject to "
            "macroeconomic and geopolitical risks that could adversely affect demand."
        ),
    )

    pack, _ = build_final_evidence_pack(
        [make_chunk_read(risk_chunk, score=0.50)],
        [],
        make_plan(),
        chunk_text_by_id={risk_chunk.id: risk_chunk.chunk_text},
    )

    assert [chunk.chunk_id for chunk in pack.risk_factor_chunks] == [132]
    assert [span.chunk_id for span in pack.risk_factor_spans] == [132]


def test_final_evidence_pack_reuses_chunk_for_multiple_roles_when_needed() -> None:
    combined_chunk = make_chunk(
        chunk_id=131,
        section="PART I - ITEM 1 - Financial Statements",
        text=(
            "CONDENSED CONSOLIDATED STATEMENTS OF OPERATIONS Total net sales $111,184.\n"
            "Products and Services Performance by category includes iPhone net sales, "
            "Services net sales, Americas, Europe, and Greater China."
        ),
        has_table=True,
    )

    pack, _ = build_final_evidence_pack(
        [make_chunk_read(combined_chunk, score=0.52)],
        [],
        make_broad_comparison_plan(),
    )

    assert [chunk.chunk_id for chunk in pack.primary_financial_statement_chunks] == [131]
    assert [chunk.chunk_id for chunk in pack.segment_or_product_breakdown_chunks] == [131]
    assert pack.segment_or_product_breakdown_spans


def test_prose_role_span_thresholds_allow_non_numeric_breakdown_evidence() -> None:
    plan = RetrievalPlan(
        **{
            **make_metric_plan().to_dict(),
            "question_type": "broad_comparison",
            "target_sections": ["Management's Discussion and Analysis"],
            "evidence_roles": ["segment_or_product_breakdown_chunks"],
        }
    )
    text = "Products and Services Performance by category includes iPhone and Mac."
    chunk = make_chunk_read(
        make_chunk(
            chunk_id=133,
            section="PART I - ITEM 2 - Management’s Discussion and Analysis",
            text=text,
        ),
        score=0.41,
    )

    spans = select_evidence_spans_for_chunk(
        chunk,
        "segment_or_product_breakdown_chunks",
        plan,
        chunk_text_by_id={chunk.chunk_id: text},
    )

    assert spans
    assert spans[0].score < 0.28
    assert "segment_or_product_context" in spans[0].reasons


def test_evidence_span_selector_prefers_metric_driver_sentence() -> None:
    plan = RetrievalPlan(
        **{
            **make_metric_plan().to_dict(),
            "target_sections": ["Management's Discussion and Analysis"],
            "comparison_basis": "latest_quarter_yoy",
            "comparison_candidates": ["latest_quarter_yoy"],
        }
    )
    chunk = make_chunk_read(
        make_chunk(
            chunk_id=150,
            section="PART I - ITEM 2 - Management’s Discussion and Analysis",
            form_type="10-Q",
            text=(
                "The company discusses many topics. "
                "Net sales increased 17% compared to the prior year primarily due to "
                "higher Services and iPhone net sales. "
                "Other administrative updates were not material."
            ),
        ),
        score=0.42,
    )

    spans = select_evidence_spans_for_chunk(
        chunk,
        "mda_explanation_chunks",
        plan,
        chunk_text_by_id={
            chunk.chunk_id: (
                "The company discusses many topics. "
                "Net sales increased 17% compared to the prior year primarily due to "
                "higher Services and iPhone net sales. "
                "Other administrative updates were not material."
            )
        },
    )

    assert spans
    assert "Net sales increased 17%" in spans[0].text
    assert spans[0].support_kind == "metric_driver"
    assert "explanatory_language" in spans[0].reasons
    assert "numeric_value" in spans[0].reasons


def test_final_evidence_pack_includes_annual_context_for_ambiguous_trend() -> None:
    pack, trace = build_final_evidence_pack(
        [
            make_chunk_read(
                make_chunk(
                    chunk_id=200,
                    section="PART I - ITEM 1 - Financial Statements",
                    form_type="10-Q",
                    text="CONDENSED CONSOLIDATED STATEMENTS OF OPERATIONS Three months ended Total net sales.",
                    has_table=True,
                ),
                score=0.50,
            ),
            make_chunk_read(
                make_chunk(
                    chunk_id=201,
                    section="PART II - ITEM 8 - Financial Statements and Supplementary Data",
                    form_type="10-K",
                    text="CONSOLIDATED STATEMENTS OF OPERATIONS Years ended Total net sales.",
                    has_table=True,
                ),
                score=0.44,
            )
        ],
        [],
        make_metric_plan(),
    )

    assert [chunk.chunk_id for chunk in pack.primary_financial_statement_chunks] == [200]
    assert [chunk.chunk_id for chunk in pack.annual_context_chunks] == [201]
    assert trace["chunk_quotas"]["annual_context_chunks"] == 1


def test_final_evidence_pack_falls_back_to_statement_chunk_for_metric_query() -> None:
    pack, _ = build_final_evidence_pack(
        [
            make_chunk_read(
                make_chunk(
                    chunk_id=202,
                    section="PART II - ITEM 8 - Financial Statements and Supplementary Data",
                    form_type="10-K",
                    text="CONSOLIDATED STATEMENTS OF OPERATIONS Years ended Total net sales.",
                    has_table=True,
                ),
                score=0.44,
            )
        ],
        [],
        make_metric_plan(),
    )

    assert [chunk.chunk_id for chunk in pack.primary_financial_statement_chunks] == [202]
    assert pack.annual_context_chunks == []


def test_final_evidence_pack_treats_explicit_fy_statement_as_primary() -> None:
    plan = RetrievalPlan(
        **{
            **make_metric_plan().to_dict(),
            "comparison_basis": "latest_fy_yoy",
            "comparison_candidates": ["latest_fy_yoy"],
            "default_comparison_basis": "latest_fy_yoy",
        }
    )
    statement_chunk = make_chunk(
        chunk_id=203,
        section="PART II - ITEM 8 - Financial Statements and Supplementary Data",
        form_type="10-K",
        text=(
            "Apple Inc. CONSOLIDATED STATEMENTS OF OPERATIONS "
            "Years ended September 27, 2025 and September 28, 2024. "
            "Net sales increased year over year."
        ),
        has_table=True,
    )

    pack, trace = build_final_evidence_pack(
        [make_chunk_read(statement_chunk, score=0.50)],
        [],
        plan,
    )

    assert [chunk.chunk_id for chunk in pack.primary_financial_statement_chunks] == [203]
    assert pack.annual_context_chunks == []
    assert trace["chunk_quotas"]["annual_context_chunks"] == 0


def test_metric_snippet_centers_on_strong_metric_match() -> None:
    text = "Tariff discussion. " * 30 + "Net sales increased to $111 billion. " + "Other text. " * 30

    snippet = make_snippet(text, max_chars=220, metric_keys=["revenue"])

    assert snippet.startswith("...")
    assert "Net sales increased" in snippet

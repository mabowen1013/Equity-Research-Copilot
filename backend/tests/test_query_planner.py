from app.core.config import Settings
from app.services import QueryPlanner
from app.services.query_planner import (
    IntentParser,
    QueryNormalizer,
    RuleMetricResolver,
    TimeScopeResolver,
    _llm_planner_system_prompt,
)


def test_query_planner_understands_selling_more_than_last_year_as_revenue() -> None:
    plan = QueryPlanner().plan("Is Apple selling more than last year?")

    assert plan.question_type == "trend"
    assert plan.metric_keys == ["revenue"]
    assert plan.time_scope == "comparison_trend"
    assert plan.comparison_basis == "latest_fy_yoy"
    assert plan.comparison_candidates == ["latest_fy_yoy"]
    assert plan.target_sections == ["Financial Statements"]
    assert "metric:revenue:sales_activity" in plan.matched_rules


def test_query_planner_treats_selling_more_as_ambiguous_revenue_trend() -> None:
    plan = QueryPlanner().plan("Is Apple selling more?")

    assert plan.question_type == "trend"
    assert plan.metric_keys == ["revenue"]
    assert plan.time_scope == "comparison_trend"
    assert plan.comparison_basis == "ambiguous"
    assert plan.comparison_candidates == [
        "latest_quarter_yoy",
        "latest_ytd_yoy",
        "latest_fy_yoy",
    ]
    assert plan.default_comparison_basis == "latest_quarter_yoy"
    assert plan.target_sections == ["Financial Statements"]
    assert plan.preferred_forms == ["10-Q"]
    assert plan.needs_metric_comparisons
    assert "metric:revenue:sales_activity" in plan.matched_rules
    assert "time:comparison_trend:sales_activity" in plan.matched_rules


def test_query_planner_understands_top_line_growth_as_revenue() -> None:
    plan = QueryPlanner().plan("How much did Apple grow its top line?")

    assert plan.question_type == "trend"
    assert plan.metric_keys == ["revenue"]
    assert plan.time_scope == "comparison_trend"
    assert plan.comparison_basis == "ambiguous"
    assert plan.comparison_candidates == [
        "latest_quarter_yoy",
        "latest_ytd_yoy",
        "latest_fy_yoy",
    ]
    assert plan.target_sections == ["Financial Statements"]
    assert "metric:revenue" in plan.matched_rules
    assert "time:comparison_trend" in plan.matched_rules


def test_query_planner_treats_metric_improvement_as_comparison() -> None:
    plan = QueryPlanner().plan("Did Apple's revenue improve?")

    assert plan.question_type == "performance_judgment"
    assert plan.metric_keys == ["revenue"]
    assert plan.time_scope == "comparison_trend"
    assert plan.comparison_basis == "ambiguous"
    assert plan.comparison_candidates == [
        "latest_quarter_yoy",
        "latest_ytd_yoy",
        "latest_fy_yoy",
    ]
    assert plan.target_sections == [
        "Financial Statements",
        "Management's Discussion and Analysis",
    ]
    assert "time:comparison_trend" in plan.matched_rules


def test_query_planner_handles_metric_get_better_language() -> None:
    plan = QueryPlanner().plan("Has Microsoft's top line gotten better?")

    assert plan.question_type == "performance_judgment"
    assert plan.metric_keys == ["revenue"]
    assert plan.time_scope == "comparison_trend"


def test_query_planner_treats_strong_latest_quarter_sales_as_judgment() -> None:
    plan = QueryPlanner().plan("Was Apple's latest quarter strong on sales?")

    assert plan.question_type == "performance_judgment"
    assert plan.metric_keys == ["revenue"]
    assert plan.time_scope == "latest"
    assert plan.comparison_basis == "latest_quarter_yoy"
    assert plan.comparison_candidates == ["latest_quarter_yoy"]
    assert plan.target_sections == [
        "Financial Statements",
        "Management's Discussion and Analysis",
    ]
    assert plan.forms == ["10-Q"]
    assert '"same quarter"' in plan.lexical_queries
    assert "section:mda_performance_judgment" in plan.matched_rules


def test_query_planner_defaults_revenue_judgment_to_latest_quarter_comparison() -> None:
    plan = QueryPlanner().plan("Was revenue strong?")

    assert plan.question_type == "performance_judgment"
    assert plan.metric_keys == ["revenue"]
    assert plan.time_scope == "latest"
    assert plan.comparison_basis == "latest_quarter_yoy"
    assert plan.comparison_candidates == ["latest_quarter_yoy"]
    assert plan.ambiguities == [
        "Interpreted performance judgment as latest comparable period because no period was specified."
    ]


def test_query_planner_treats_persistent_sales_judgment_as_ambiguous_trend() -> None:
    plan = QueryPlanner().plan("Are sales still strong?")

    assert plan.question_type == "performance_judgment"
    assert plan.metric_keys == ["revenue"]
    assert plan.time_scope == "comparison_trend"
    assert plan.comparison_basis == "ambiguous"
    assert plan.comparison_candidates == [
        "latest_quarter_yoy",
        "latest_ytd_yoy",
        "latest_fy_yoy",
    ]
    assert plan.target_sections == [
        "Financial Statements",
        "Management's Discussion and Analysis",
    ]
    assert plan.preferred_forms == ["10-Q"]
    assert plan.needs_metric_comparisons
    assert "time:comparison_trend:persistent_judgment" in plan.matched_rules


def test_query_planner_treats_persistent_profitability_judgment_as_ambiguous_trend() -> None:
    plan = QueryPlanner().plan("Did profitability remain robust?")

    assert plan.question_type == "performance_judgment"
    assert plan.metric_keys == ["operating_income", "net_income"]
    assert plan.time_scope == "comparison_trend"
    assert plan.comparison_basis == "ambiguous"
    assert plan.comparison_candidates == [
        "latest_quarter_yoy",
        "latest_ytd_yoy",
        "latest_fy_yoy",
    ]
    assert "metric:profitability_default" in plan.matched_rules
    assert "time:comparison_trend:persistent_judgment" in plan.matched_rules


def test_query_planner_defaults_profitability_judgment_to_profit_metrics() -> None:
    plan = QueryPlanner().plan("Was profitability weak?")

    assert plan.question_type == "performance_judgment"
    assert plan.metric_keys == ["operating_income", "net_income"]
    assert plan.comparison_basis == "latest_quarter_yoy"
    assert "metric:profitability_default" in plan.matched_rules


def test_query_planner_treats_comparative_profitability_as_ambiguous_trend() -> None:
    plan = QueryPlanner().plan("Is Apple more profitable?")

    assert plan.question_type == "performance_judgment"
    assert plan.metric_keys == ["operating_income", "net_income"]
    assert plan.time_scope == "comparison_trend"
    assert plan.comparison_basis == "ambiguous"
    assert plan.comparison_candidates == [
        "latest_quarter_yoy",
        "latest_ytd_yoy",
        "latest_fy_yoy",
    ]
    assert plan.target_sections == [
        "Financial Statements",
        "Management's Discussion and Analysis",
    ]
    assert plan.preferred_forms == ["10-Q"]
    assert plan.needs_financial_facts
    assert plan.needs_metric_comparisons
    assert "intent:metric_comparative_judgment" in plan.matched_rules
    assert "time:comparison_trend:metric_comparative" in plan.matched_rules


def test_query_planner_treats_metric_higher_as_ambiguous_judgment() -> None:
    plan = QueryPlanner().plan("Was net income higher?")

    assert plan.question_type == "performance_judgment"
    assert plan.metric_keys == ["net_income"]
    assert plan.time_scope == "comparison_trend"
    assert plan.comparison_basis == "ambiguous"
    assert plan.comparison_candidates == [
        "latest_quarter_yoy",
        "latest_ytd_yoy",
        "latest_fy_yoy",
    ]
    assert plan.needs_metric_comparisons


def test_query_planner_treats_making_more_money_as_net_income_trend() -> None:
    plan = QueryPlanner().plan("Did Apple make more money?")

    assert plan.question_type == "performance_judgment"
    assert plan.metric_keys == ["net_income"]
    assert plan.time_scope == "comparison_trend"
    assert plan.comparison_basis == "ambiguous"
    assert plan.comparison_candidates == [
        "latest_quarter_yoy",
        "latest_ytd_yoy",
        "latest_fy_yoy",
    ]
    assert plan.needs_metric_comparisons
    assert "metric:net_income:earnings_activity" in plan.matched_rules


def test_query_planner_does_not_treat_more_about_metric_as_comparative() -> None:
    plan = QueryPlanner().plan("Tell me more about revenue.")

    assert plan.metric_keys == ["revenue"]
    assert plan.time_scope == "unspecified"
    assert plan.comparison_basis == "none"
    assert "intent:metric_comparative_judgment" not in plan.matched_rules


def test_query_planner_defaults_margin_judgment_to_margin_metrics() -> None:
    plan = QueryPlanner().plan("Was margin performance solid?")

    assert plan.question_type == "performance_judgment"
    assert plan.metric_keys == ["gross_margin", "operating_margin", "net_margin"]
    assert plan.comparison_basis == "latest_quarter_yoy"
    assert "metric:margin_default" in plan.matched_rules


def test_query_planner_defaults_strong_quarter_to_core_growth_metrics() -> None:
    plan = QueryPlanner().plan("Did Apple have a strong quarter?")

    assert plan.question_type == "performance_judgment"
    assert plan.metric_keys == ["revenue", "operating_income", "net_income"]
    assert plan.comparison_basis == "latest_quarter_yoy"
    assert "metric:company_growth_default" in plan.matched_rules


def test_query_planner_treats_performing_well_as_performance_judgment() -> None:
    plan = QueryPlanner().plan("Is Apple performing well?")

    assert plan.question_type == "performance_judgment"
    assert plan.metric_keys == ["revenue", "operating_income", "net_income"]
    assert plan.time_scope == "latest"
    assert plan.comparison_basis == "latest_quarter_yoy"
    assert plan.target_sections == [
        "Financial Statements",
        "Management's Discussion and Analysis",
    ]
    assert plan.preferred_forms == ["10-Q"]
    assert plan.needs_financial_facts
    assert plan.needs_metric_comparisons
    assert "metric:company_growth_default" in plan.matched_rules


def test_query_planner_defaults_broad_company_growth_to_core_metrics() -> None:
    plan = QueryPlanner().plan("Is Apple growing?")

    assert plan.question_type == "trend"
    assert plan.metric_keys == ["revenue", "operating_income", "net_income"]
    assert plan.time_scope == "comparison_trend"
    assert plan.comparison_basis == "ambiguous"
    assert plan.comparison_candidates == [
        "latest_quarter_yoy",
        "latest_ytd_yoy",
        "latest_fy_yoy",
    ]
    assert plan.target_sections == [
        "Financial Statements",
        "Management's Discussion and Analysis",
    ]
    assert plan.forms == []
    assert plan.preferred_forms == ["10-Q"]
    assert '"products and services performance"' in plan.lexical_queries
    assert '"segment operating performance"' in plan.lexical_queries
    assert '"operating income"' in plan.lexical_queries
    assert '"net income"' in plan.lexical_queries
    assert "metric:company_growth_default" in plan.matched_rules


def test_query_planner_builds_slot_dense_query_for_broad_growth() -> None:
    plan = QueryPlanner().plan("Is Apple growing?")

    assert plan.dense_queries[0].startswith(
        "year-over-year quarterly year-to-date annual trend"
    )
    assert "revenue total net sales net sales" in plan.dense_queries[0]
    assert "operating income income from operations" in plan.dense_queries[0]
    assert "financial statements statements of operations" in plan.dense_queries[0]
    assert "management discussion analysis results of operations" in plan.dense_queries[0]
    assert any(
        query.startswith("management discussion analysis")
        for query in plan.dense_queries
    )
    assert plan.dense_queries[-1] == "Is Apple growing?"
    assert [spec["text"] for spec in plan.dense_query_specs] == plan.dense_queries
    assert plan.dense_query_specs[0]["role"] == "slot"
    assert plan.dense_query_specs[0]["weight"] == 1.0
    assert plan.dense_query_specs[-1] == {
        "role": "original",
        "text": "Is Apple growing?",
        "weight": 0.4,
    }


def test_query_planner_defaults_vague_last_year_change_to_company_comparison() -> None:
    plan = QueryPlanner().plan("What changed compared with last year?")

    assert plan.question_type == "broad_comparison"
    assert plan.metric_keys == [
        "revenue",
        "gross_margin",
        "operating_income",
        "net_income",
    ]
    assert plan.time_scope == "comparison_trend"
    assert plan.comparison_basis == "latest_fy_yoy"
    assert plan.comparison_candidates == ["latest_fy_yoy"]
    assert plan.default_comparison_basis == "latest_fy_yoy"
    assert plan.forms == ["10-K"]
    assert plan.target_sections == [
        "Financial Statements",
        "Management's Discussion and Analysis",
    ]
    assert plan.dense_queries[0].startswith("latest fiscal year year ended annual")
    assert "financial performance changes" in plan.dense_queries[0]
    assert "year-over-year compared to prior fiscal year" in plan.dense_queries[0]
    assert any(
        query.startswith("management discussion analysis")
        for query in plan.dense_queries
    )
    assert any(
        query.startswith("products and services performance")
        for query in plan.dense_queries
    )
    assert plan.dense_queries[-1] == "What changed compared with last year?"
    assert '"products and services performance"' in plan.lexical_queries
    assert '"segment operating performance"' in plan.lexical_queries
    assert '"gross margin" "compared to"' in plan.lexical_queries
    assert "metric:company_change_default" in plan.matched_rules


def test_query_planner_defaults_last_quarter_do_question_to_performance_overview() -> None:
    plan = QueryPlanner().plan("How did Apple do last quarter?")

    assert plan.question_type == "performance_overview"
    assert plan.metric_keys == [
        "revenue",
        "gross_margin",
        "operating_income",
        "net_income",
    ]
    assert plan.time_scope == "latest"
    assert plan.comparison_basis == "latest_quarter_yoy"
    assert plan.comparison_candidates == ["latest_quarter_yoy"]
    assert plan.default_comparison_basis == "latest_quarter_yoy"
    assert plan.forms == ["10-Q"]
    assert plan.target_sections == [
        "Financial Statements",
        "Management's Discussion and Analysis",
    ]
    assert plan.dense_queries[0].startswith("latest quarter three months ended")
    assert "financial performance results" in plan.dense_queries[0]
    assert "year-over-year compared to same quarter prior year" in plan.dense_queries[0]
    assert any(
        query.startswith("management discussion analysis")
        for query in plan.dense_queries
    )
    assert any(
        query.startswith("products and services performance")
        for query in plan.dense_queries
    )
    assert plan.dense_queries[-1] == "How did Apple do last quarter?"
    assert '"three months ended" "net sales"' in plan.lexical_queries
    assert '"products and services performance"' in plan.lexical_queries
    assert '"segment operating performance"' in plan.lexical_queries
    assert "metric:company_performance_default" in plan.matched_rules


def test_query_planner_does_not_default_stock_performance_to_financial_metrics() -> None:
    plan = QueryPlanner().plan("How did Apple stock do last quarter?")

    assert plan.metric_keys == []
    assert "metric:company_performance_default" not in plan.matched_rules


def test_query_planner_does_not_default_accounting_controls_change_to_financial_metrics() -> None:
    plan = QueryPlanner().plan("What changed in accounting controls compared with last year?")

    assert plan.metric_keys == []
    assert "metric:company_change_default" not in plan.matched_rules


def test_query_planner_detects_growth_acceleration_intent() -> None:
    plan = QueryPlanner().plan("Is AAPL growth accelerating?")

    assert plan.question_type == "growth_acceleration"
    assert plan.metric_keys == ["revenue", "operating_income", "net_income"]
    assert plan.time_scope == "comparison_trend"
    assert plan.comparison_basis == "latest_quarter_yoy"
    assert plan.comparison_candidates == [
        "latest_quarter_yoy",
        "previous_quarter_yoy",
    ]
    assert plan.default_comparison_basis == "latest_quarter_yoy"
    assert plan.forms == ["10-Q"]
    assert plan.target_sections == [
        "Financial Statements",
        "Management's Discussion and Analysis",
    ]
    assert '"growth accelerated"' in plan.lexical_queries
    assert '"year-over-year" "growth"' in plan.lexical_queries
    assert "comparison_basis:growth_acceleration" in plan.matched_rules


def test_query_planner_detects_explicit_revenue_growth_deceleration() -> None:
    plan = QueryPlanner().plan("Is revenue growth decelerating?")

    assert plan.question_type == "growth_acceleration"
    assert plan.metric_keys == ["revenue"]
    assert plan.comparison_candidates == [
        "latest_quarter_yoy",
        "previous_quarter_yoy",
    ]
    assert plan.target_sections == [
        "Financial Statements",
        "Management's Discussion and Analysis",
    ]


def test_query_planner_detects_annual_growth_acceleration_basis() -> None:
    plan = QueryPlanner().plan("Is annual revenue growth accelerating?")

    assert plan.question_type == "growth_acceleration"
    assert plan.metric_keys == ["revenue"]
    assert plan.comparison_basis == "latest_fy_yoy"
    assert plan.comparison_candidates == [
        "latest_fy_yoy",
        "previous_fy_yoy",
    ]
    assert plan.default_comparison_basis == "latest_fy_yoy"
    assert plan.forms == ["10-K"]
    assert plan.ambiguities == []


def test_query_planner_does_not_default_specific_unsupported_growth_subjects() -> None:
    plan = QueryPlanner().plan("Is Apple's debt growing?")

    assert plan.metric_keys == []
    assert "metric:company_growth_default" not in plan.matched_rules


def test_query_planner_does_not_default_cash_burn_acceleration_to_company_growth() -> None:
    plan = QueryPlanner().plan("Is cash burn accelerating?")

    assert plan.metric_keys == []
    assert "metric:company_growth_default" not in plan.matched_rules


def test_query_planner_handles_non_apple_revenue_aliases() -> None:
    plan = QueryPlanner().plan("How fast did Toyota turnover expand?")

    assert plan.question_type == "trend"
    assert plan.metric_keys == ["revenue"]
    assert plan.time_scope == "comparison_trend"


def test_query_planner_generalizes_sell_more_phrasing_across_companies() -> None:
    plan = QueryPlanner().plan("Did Microsoft sell more than the prior year?")

    assert plan.question_type == "trend"
    assert plan.metric_keys == ["revenue"]
    assert plan.comparison_basis == "latest_fy_yoy"


def test_query_planner_does_not_treat_selling_expenses_as_revenue() -> None:
    plan = QueryPlanner().plan("What were Apple's selling expenses?")

    assert plan.metric_keys == []
    assert "metric:revenue:sales_activity" not in plan.matched_rules


def test_query_planner_does_not_make_last_year_value_question_a_comparison() -> None:
    plan = QueryPlanner().plan("What was Apple's revenue last year?")

    assert plan.metric_keys == ["revenue"]
    assert plan.question_type == "metric"
    assert plan.comparison_basis == "none"


def test_query_planner_targets_risk_factor_sections() -> None:
    plan = QueryPlanner().plan("What are Apple's latest risk factors?")

    assert plan.question_type == "risk"
    assert plan.target_sections == ["Risk Factors"]
    assert plan.time_scope == "latest"
    assert "section:risk_factors" in plan.matched_rules


def test_query_planner_detects_metric_and_mda_mixed_query() -> None:
    plan = QueryPlanner().plan("Why did gross margin change according to MD&A?")

    assert plan.question_type == "mixed"
    assert plan.target_sections == ["Management's Discussion and Analysis"]
    assert plan.metric_keys == ["gross_margin"]
    assert "section:mda" in plan.matched_rules
    assert "metric:gross_margin" in plan.matched_rules


def test_query_planner_supports_chinese_metric_and_time_terms() -> None:
    plan = QueryPlanner().plan("最近营收同比变化如何？")

    assert plan.question_type == "trend"
    assert plan.metric_keys == ["revenue"]
    assert plan.time_scope == "comparison_trend"
    assert "metric:revenue" in plan.matched_rules


def test_query_planner_expands_revenue_growth_queries() -> None:
    plan = QueryPlanner().plan("What was Apple revenue growth?")

    assert plan.question_type == "trend"
    assert plan.metric_keys == ["revenue"]
    assert plan.time_scope == "comparison_trend"
    assert plan.comparison_basis == "ambiguous"
    assert plan.comparison_candidates == [
        "latest_quarter_yoy",
        "latest_ytd_yoy",
        "latest_fy_yoy",
    ]
    assert plan.default_comparison_basis == "latest_quarter_yoy"
    assert plan.ambiguities == [
        "Question does not specify quarterly, year-to-date, annual, or multi-year growth."
    ]
    assert plan.target_sections == ["Financial Statements"]
    assert '"total net sales"' in plan.lexical_queries
    assert '"revenue growth"' in plan.lexical_queries
    assert '"condensed consolidated statements of operations"' in plan.lexical_queries
    assert "revenue" not in plan.lexical_queries
    assert "time:comparison_trend" in plan.matched_rules


def test_query_planner_defaults_fuzzy_metric_performance_to_latest_quarter_yoy() -> None:
    plan = QueryPlanner().plan("How did Apple sales do?")

    assert plan.question_type == "trend"
    assert plan.metric_keys == ["revenue"]
    assert plan.time_scope == "latest"
    assert plan.comparison_basis == "latest_quarter_yoy"
    assert plan.comparison_candidates == ["latest_quarter_yoy"]
    assert plan.default_comparison_basis == "latest_quarter_yoy"
    assert plan.target_sections == [
        "Financial Statements",
        "Management's Discussion and Analysis",
    ]
    assert plan.ambiguities == [
        "Interpreted as latest comparable period performance because no period was specified."
    ]
    assert "time:latest_default_metric_performance" in plan.matched_rules
    assert "comparison_basis:latest_quarter_yoy:default" in plan.matched_rules


def test_query_planner_expands_non_revenue_metric_growth_queries() -> None:
    plan = QueryPlanner().plan("How did Apple net income growth change?")

    assert plan.question_type == "trend"
    assert plan.metric_keys == ["net_income"]
    assert plan.time_scope == "comparison_trend"
    assert plan.comparison_basis == "ambiguous"
    assert plan.target_sections == ["Financial Statements"]
    assert '"net income"' in plan.lexical_queries
    assert '"net income increased"' in plan.lexical_queries
    assert '"statements of operations"' in plan.lexical_queries


def test_query_planner_detects_explicit_annual_growth_basis() -> None:
    plan = QueryPlanner().plan("What was Apple annual revenue growth?")

    assert plan.question_type == "trend"
    assert plan.comparison_basis == "latest_fy_yoy"
    assert plan.comparison_candidates == ["latest_fy_yoy"]
    assert plan.default_comparison_basis == "latest_fy_yoy"
    assert plan.ambiguities == []


def test_query_planner_routes_metric_reason_questions_to_mda() -> None:
    plan = QueryPlanner().plan("Tesla free cash flow 最近变化的原因是什么？")

    assert plan.question_type == "mixed"
    assert plan.metric_keys == ["free_cash_flow"]
    assert plan.target_sections == ["Management's Discussion and Analysis"]
    assert any(rule.startswith("section:mda") for rule in plan.matched_rules)


def test_query_planner_routes_driver_questions_to_mda_without_explicit_mda_term() -> None:
    plan = QueryPlanner().plan("Why did free cash flow decline?")

    assert plan.question_type == "mixed"
    assert plan.metric_keys == ["free_cash_flow"]
    assert "Management's Discussion and Analysis" in plan.target_sections
    assert "section:mda_metric_explanation" in plan.matched_rules


def test_query_planner_routes_bare_driver_questions_to_mda_without_default_metrics() -> None:
    plan = QueryPlanner().plan("What were the main drivers?")

    assert plan.question_type == "prose"
    assert plan.metric_keys == []
    assert plan.target_sections == ["Management's Discussion and Analysis"]
    assert not plan.needs_financial_facts
    assert not plan.needs_metric_comparisons
    assert "mda_explanation_chunks" in plan.evidence_roles
    assert "management discussion analysis" in plan.lexical_queries
    assert "section:mda_driver_context" in plan.matched_rules


def test_query_planner_does_not_route_stock_price_driver_questions_to_mda() -> None:
    plan = QueryPlanner().plan("What drove Apple's stock price?")

    assert plan.question_type == "prose"
    assert plan.metric_keys == []
    assert plan.target_sections == []
    assert "section:mda_driver_context" not in plan.matched_rules


def test_query_planner_defaults_performance_change_explanations_to_company_change_metrics() -> None:
    plan = QueryPlanner().plan("Why did performance change?")

    assert plan.question_type == "mixed"
    assert plan.metric_keys == [
        "revenue",
        "gross_margin",
        "operating_income",
        "net_income",
    ]
    assert plan.time_scope == "comparison_trend"
    assert plan.comparison_basis == "ambiguous"
    assert plan.comparison_candidates == [
        "latest_quarter_yoy",
        "latest_ytd_yoy",
        "latest_fy_yoy",
    ]
    assert plan.target_sections == [
        "Financial Statements",
        "Management's Discussion and Analysis",
    ]
    assert plan.preferred_forms == ["10-Q"]
    assert plan.needs_financial_facts
    assert plan.needs_metric_comparisons
    assert "metric:company_change_default:performance_explanation" in plan.matched_rules


def test_query_planner_defaults_causal_performance_change_phrasing_to_company_change_metrics() -> None:
    plan = QueryPlanner().plan("What caused business performance to change?")

    assert plan.question_type == "mixed"
    assert plan.metric_keys == [
        "revenue",
        "gross_margin",
        "operating_income",
        "net_income",
    ]
    assert plan.comparison_basis == "ambiguous"
    assert "metric:company_change_default:performance_explanation" in plan.matched_rules


def test_query_planner_defaults_performance_driver_questions_to_latest_company_performance() -> None:
    plan = QueryPlanner().plan("What drove Apple's performance?")

    assert plan.question_type == "mixed"
    assert plan.metric_keys == [
        "revenue",
        "gross_margin",
        "operating_income",
        "net_income",
    ]
    assert plan.time_scope == "latest"
    assert plan.comparison_basis == "latest_quarter_yoy"
    assert plan.comparison_candidates == ["latest_quarter_yoy"]
    assert plan.target_sections == [
        "Financial Statements",
        "Management's Discussion and Analysis",
    ]
    assert plan.preferred_forms == ["10-Q"]
    assert plan.needs_financial_facts
    assert plan.needs_metric_comparisons
    assert "metric:company_performance_default:driver_explanation" in plan.matched_rules


def test_query_planner_defaults_results_driver_questions_to_latest_company_performance() -> None:
    plan = QueryPlanner().plan("What factors drove the financial results?")

    assert plan.question_type == "mixed"
    assert plan.metric_keys == [
        "revenue",
        "gross_margin",
        "operating_income",
        "net_income",
    ]
    assert plan.time_scope == "latest"
    assert plan.comparison_basis == "latest_quarter_yoy"
    assert "metric:company_performance_default:driver_explanation" in plan.matched_rules


def test_query_planner_detects_current_liquidity_questions() -> None:
    plan = QueryPlanner().plan("How is Apple's liquidity?")

    assert plan.question_type == "liquidity"
    assert plan.metric_keys == ["operating_cash_flow", "free_cash_flow"]
    assert plan.time_scope == "latest"
    assert plan.comparison_basis == "latest_quarter_yoy"
    assert plan.comparison_candidates == ["latest_quarter_yoy"]
    assert plan.target_sections == [
        "Liquidity",
        "Cash Flows",
        "Management's Discussion and Analysis",
    ]
    assert plan.forms == ["10-Q"]
    assert plan.preferred_forms == ["10-Q"]
    assert plan.needs_financial_facts
    assert plan.needs_metric_comparisons
    assert "intent:liquidity" in plan.matched_rules
    assert "metric:liquidity_default" in plan.matched_rules


def test_query_planner_detects_cash_sufficiency_without_forced_comparison() -> None:
    plan = QueryPlanner().plan("Does Apple have enough cash?")

    assert plan.question_type == "liquidity"
    assert plan.metric_keys == ["operating_cash_flow", "free_cash_flow"]
    assert plan.time_scope == "latest"
    assert plan.comparison_basis == "none"
    assert plan.comparison_candidates == []
    assert plan.target_sections == [
        "Liquidity",
        "Cash Flows",
        "Management's Discussion and Analysis",
    ]
    assert plan.forms == ["10-Q"]
    assert plan.preferred_forms == ["10-Q"]
    assert plan.needs_financial_facts
    assert not plan.needs_metric_comparisons


def test_query_planner_treats_generic_cash_flow_improvement_as_trend() -> None:
    plan = QueryPlanner().plan("Did cash flow improve?")

    assert plan.question_type == "trend"
    assert plan.metric_keys == ["operating_cash_flow", "free_cash_flow"]
    assert plan.time_scope == "comparison_trend"
    assert plan.comparison_basis == "ambiguous"
    assert plan.comparison_candidates == [
        "latest_quarter_yoy",
        "latest_ytd_yoy",
        "latest_fy_yoy",
    ]
    assert plan.target_sections == [
        "Cash Flows",
        "Management's Discussion and Analysis",
    ]
    assert plan.needs_metric_comparisons
    assert "primary_financial_statement_chunks" in plan.evidence_roles
    assert "mda_explanation_chunks" in plan.evidence_roles


def test_query_planner_does_not_treat_cash_burn_as_liquidity_default() -> None:
    plan = QueryPlanner().plan("Is cash burn accelerating?")

    assert plan.metric_keys == []
    assert "metric:liquidity_default" not in plan.matched_rules


def test_query_planner_summarizes_latest_earnings_report() -> None:
    plan = QueryPlanner().plan("Summarize Apple's latest earnings report.")

    assert plan.question_type == "filing_summary"
    assert plan.metric_keys == [
        "revenue",
        "gross_margin",
        "operating_income",
        "net_income",
        "operating_cash_flow",
        "free_cash_flow",
    ]
    assert plan.time_scope == "latest"
    assert plan.comparison_basis == "none"
    assert plan.comparison_candidates == []
    assert plan.target_sections == [
        "Financial Statements",
        "Management's Discussion and Analysis",
        "Liquidity",
        "Cash Flows",
    ]
    assert plan.forms == ["10-Q"]
    assert plan.preferred_forms == ["10-Q"]
    assert plan.needs_financial_facts
    assert not plan.needs_metric_comparisons
    assert "primary_financial_statement_chunks" in plan.evidence_roles
    assert "mda_explanation_chunks" in plan.evidence_roles
    assert "metric:filing_summary_default" in plan.matched_rules


def test_query_planner_summarizes_latest_10k_with_risk_factors() -> None:
    plan = QueryPlanner().plan("Summarize Apple's latest 10-K.")

    assert plan.question_type == "filing_summary"
    assert plan.forms == ["10-K"]
    assert plan.preferred_forms == ["10-K"]
    assert "Risk Factors" in plan.target_sections
    assert "risk_factor_chunks" in plan.evidence_roles
    assert not plan.needs_metric_comparisons


def test_query_planner_does_not_treat_generic_summary_as_filing_summary() -> None:
    plan = QueryPlanner().plan("Summarize Apple.")

    assert plan.question_type == "prose"
    assert plan.metric_keys == []
    assert "intent:filing_summary" not in plan.matched_rules


def test_query_planner_keeps_risk_and_liquidity_summary_specific() -> None:
    risk_plan = QueryPlanner().plan("Summarize Apple's risks.")
    liquidity_plan = QueryPlanner().plan("Summarize Apple's liquidity.")

    assert risk_plan.question_type == "risk"
    assert risk_plan.target_sections == ["Risk Factors"]
    assert risk_plan.metric_keys == []
    assert "risk_factor_chunks" in risk_plan.evidence_roles

    assert liquidity_plan.question_type == "liquidity"
    assert liquidity_plan.metric_keys == ["operating_cash_flow", "free_cash_flow"]
    assert liquidity_plan.target_sections == [
        "Liquidity",
        "Cash Flows",
        "Management's Discussion and Analysis",
    ]


def test_query_planner_treats_recently_as_latest_performance_overview() -> None:
    plan = QueryPlanner().plan("How did Apple do recently?")

    assert plan.question_type == "performance_overview"
    assert plan.metric_keys == [
        "revenue",
        "gross_margin",
        "operating_income",
        "net_income",
    ]
    assert plan.time_scope == "latest"
    assert plan.comparison_basis == "latest_quarter_yoy"
    assert plan.comparison_candidates == ["latest_quarter_yoy"]
    assert plan.default_comparison_basis == "latest_quarter_yoy"
    assert plan.target_sections == [
        "Financial Statements",
        "Management's Discussion and Analysis",
    ]
    assert plan.needs_financial_facts
    assert plan.needs_metric_comparisons
    assert "time:latest" in plan.matched_rules
    assert "metric:company_performance_default" in plan.matched_rules


def test_query_planner_treats_doing_now_as_latest_performance_overview() -> None:
    plan = QueryPlanner().plan("How is Apple doing now?")

    assert plan.question_type == "performance_overview"
    assert plan.metric_keys == [
        "revenue",
        "gross_margin",
        "operating_income",
        "net_income",
    ]
    assert plan.time_scope == "latest"
    assert plan.comparison_basis == "latest_quarter_yoy"
    assert plan.comparison_candidates == ["latest_quarter_yoy"]
    assert plan.forms == ["10-Q"]
    assert plan.target_sections == [
        "Financial Statements",
        "Management's Discussion and Analysis",
    ]
    assert plan.needs_financial_facts
    assert plan.needs_metric_comparisons
    assert "intent:current_performance" in plan.matched_rules
    assert "time:latest" in plan.matched_rules


def test_prose_without_metrics_does_not_get_artificial_high_confidence() -> None:
    plan = QueryPlanner().plan("Tell me about Apple.")

    assert plan.question_type == "prose"
    assert plan.metric_keys == []
    assert plan.rule_confidence < 0.75
    assert plan.requires_llm_fallback_reason == "ambiguous_intent"


def test_query_planner_exposes_slot_confidence_and_strategy_flags() -> None:
    plan = QueryPlanner().plan("Was revenue strong?")

    assert plan.planner_source == "rule_validated"
    assert plan.rule_confidence == plan.confidence_breakdown["overall_confidence"]
    assert set(plan.confidence_breakdown) == {
        "intent_confidence",
        "metric_confidence",
        "time_confidence",
        "strategy_confidence",
        "validation_confidence",
        "overall_confidence",
    }
    assert plan.needs_financial_facts
    assert plan.needs_text_chunks
    assert plan.needs_metric_comparisons
    assert "metric_comparisons" in plan.evidence_roles


def test_metric_resolver_uses_metric_profile_aliases() -> None:
    normalizer = QueryNormalizer()
    query = normalizer.normalize("Did the bottom line improve?")
    intent = IntentParser().parse(query)

    result = RuleMetricResolver().resolve(query, intent)

    assert result.metric_keys == ["net_income"]
    assert "metric:net_income" in result.matched_rules
    assert result.confidence >= 0.9


def test_time_resolver_marks_conflicting_period_basis() -> None:
    query = QueryNormalizer().normalize("Did revenue improve latest quarter compared with last year?")
    intent = IntentParser().parse(query)
    metrics = RuleMetricResolver().resolve(query, intent)

    result = TimeScopeResolver().resolve(query, intent, metrics.metric_keys)

    assert result.has_conflict
    assert result.confidence < 0.75


class FakeLLMPlanner:
    def __init__(self, candidate: dict | None = None, *, should_raise: bool = False) -> None:
        self.candidate = candidate or {}
        self.should_raise = should_raise
        self.called = False

    def plan_candidate(self, question: str) -> dict:
        self.called = True
        if self.should_raise:
            raise RuntimeError("boom")
        return self.candidate


def test_rule_only_mode_does_not_call_llm_fallback() -> None:
    fake_llm = FakeLLMPlanner({"question_type": "risk", "confidence": 0.9})
    planner = QueryPlanner(
        settings=Settings(_env_file=None, query_planner_mode="rule_only"),
        llm_planner=fake_llm,
    )

    plan = planner.plan("Tell me about Apple.")

    assert not fake_llm.called
    assert plan.planner_source == "rule_validated"


def test_llm_fallback_runs_for_low_confidence_rule_plan() -> None:
    fake_llm = FakeLLMPlanner(
        {
            "question_type": "trend",
            "metric_keys": ["revenue"],
            "time_scope": "comparison_trend",
            "comparison_basis": "latest_fy_yoy",
            "comparison_candidates": ["latest_fy_yoy"],
            "target_sections": ["Financial Statements"],
            "forms": [],
            "confidence": 0.91,
        }
    )
    planner = QueryPlanner(
        settings=Settings(_env_file=None, query_planner_mode="rule_with_llm_fallback"),
        llm_planner=fake_llm,
    )

    plan = planner.plan("Tell me about Apple.")

    assert fake_llm.called
    assert plan.planner_source == "llm_validated"
    assert plan.requires_llm_fallback_reason == "ambiguous_intent"
    assert "llm_fallback:ambiguous_intent" in plan.matched_rules
    assert plan.metric_keys == ["revenue"]
    assert plan.forms == ["10-K"]
    assert plan.preferred_forms == ["10-K"]
    assert plan.needs_metric_comparisons
    assert plan.dense_query_specs[0]["role"] == "slot"


def test_llm_ambiguous_comparison_candidates_enable_metric_comparisons() -> None:
    fake_llm = FakeLLMPlanner(
        {
            "question_type": "trend",
            "metric_keys": ["revenue", "operating_income", "net_income"],
            "time_scope": "comparison_trend",
            "comparison_basis": "ambiguous",
            "comparison_candidates": [
                "latest_quarter_yoy",
                "latest_ytd_yoy",
                "latest_fy_yoy",
            ],
            "target_sections": [
                "Financial Statements",
                "Management's Discussion and Analysis",
            ],
            "forms": [],
            "confidence": 0.84,
        }
    )
    planner = QueryPlanner(
        settings=Settings(_env_file=None, query_planner_mode="rule_with_llm_fallback"),
        llm_planner=fake_llm,
    )

    plan = planner.plan("Tell me about Apple.")

    assert fake_llm.called
    assert plan.planner_source == "llm_validated"
    assert plan.comparison_basis == "ambiguous"
    assert plan.comparison_candidates == [
        "latest_quarter_yoy",
        "latest_ytd_yoy",
        "latest_fy_yoy",
    ]
    assert plan.default_comparison_basis == "latest_quarter_yoy"
    assert plan.preferred_forms == ["10-Q"]
    assert plan.needs_metric_comparisons
    assert "metric_comparisons" in plan.evidence_roles


def test_invalid_llm_candidate_falls_back_to_rule_plan() -> None:
    fake_llm = FakeLLMPlanner({"unexpected": "field"})
    planner = QueryPlanner(
        settings=Settings(_env_file=None, query_planner_mode="rule_with_llm_fallback"),
        llm_planner=fake_llm,
    )

    plan = planner.plan("Tell me about Apple.")

    assert fake_llm.called
    assert plan.planner_source == "rule_validated"
    assert "llm_fallback:failed" in plan.matched_rules
    assert plan.requires_llm_fallback_reason == "ambiguous_intent"


def test_llm_planner_prompt_lists_schema_enums_and_examples() -> None:
    prompt = _llm_planner_system_prompt()

    assert "Allowed question_type values" in prompt
    assert "- performance_overview" in prompt
    assert "- liquidity" in prompt
    assert "- filing_summary" in prompt
    assert "- revenue" in prompt
    assert "- latest_quarter_yoy" in prompt
    assert "- Financial Statements" in prompt
    assert "- 10-Q" in prompt
    assert "Do not invent metrics" in prompt
    assert "User: Does Apple have enough cash?" in prompt
    assert "User: Summarize Apple's latest earnings report." in prompt
    assert "User: How is Apple doing now?" in prompt

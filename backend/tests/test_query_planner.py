from app.core.config import Settings
from app.services.query_planner import (
    LLMQueryPlanner,
    PlanValidator,
    QueryNormalizer,
    QueryPlanner,
    _llm_dense_query_rewriter_payload,
    _llm_dense_query_rewriter_system_prompt,
    _llm_planner_system_prompt,
)


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


class FakeDenseQueryRewriter:
    def __init__(self, specs: list[dict]) -> None:
        self.specs = specs
        self.called = False
        self.requested_roles: list[str] = []

    def rewrite(self, *, question: str, plan, requested_roles: list[str]) -> list[dict]:
        self.called = True
        self.requested_roles = requested_roles
        return self.specs


def test_llm_mode_uses_llm_candidate_as_planner_source() -> None:
    fake_llm = FakeLLMPlanner(
        {
            "question_type": "trend",
            "metric_keys": ["revenue"],
            "time_scope": "comparison_trend",
            "comparison_basis": "latest_fy_yoy",
            "comparison_candidates": ["latest_fy_yoy"],
            "target_sections": ["Financial Statements"],
            "forms": [],
        }
    )
    planner = QueryPlanner(
        settings=Settings(_env_file=None, query_planner_mode="llm"),
        llm_planner=fake_llm,
    )

    plan = planner.plan("What was Apple revenue growth?")

    assert fake_llm.called
    assert plan.planner_source == "llm_validated"
    assert plan.question_type == "trend"
    assert plan.metric_keys == ["revenue"]
    assert plan.default_comparison_basis == "latest_fy_yoy"
    assert plan.forms == []
    assert plan.allowed_forms == ["10-K"]
    assert plan.preferred_forms == ["10-K"]
    assert plan.needs_financial_facts
    assert plan.needs_metric_comparisons
    assert any(spec["role"] == "financial_statement" for spec in plan.dense_query_specs)
    assert '"total net sales"' in plan.lexical_queries


def test_llm_mode_uses_dense_query_rewriter_for_valid_specs() -> None:
    fake_llm = FakeLLMPlanner(
        {
            "question_type": "mixed",
            "metric_keys": ["revenue", "gross_margin"],
            "time_scope": "latest",
            "comparison_basis": "latest_quarter_yoy",
            "comparison_candidates": ["latest_quarter_yoy"],
            "target_sections": [
                "Financial Statements",
                "Management's Discussion and Analysis",
            ],
            "forms": ["10-Q"],
        }
    )
    fake_rewriter = FakeDenseQueryRewriter(
        [
            {
                "role": "slot",
                "text": "latest quarterly financial performance showing year over year revenue and gross margin changes",
            },
            {
                "role": "financial_statement",
                "text": "consolidated statements of operations showing three months ended net sales gross margin and cost of sales",
            },
            {
                "role": "mda",
                "text": "results of operations discussion explaining revenue and gross margin drivers compared to the prior year quarter",
            },
        ]
    )
    planner = QueryPlanner(
        settings=Settings(_env_file=None, query_planner_mode="llm"),
        llm_planner=fake_llm,
        dense_query_rewriter=fake_rewriter,
    )

    plan = planner.plan("How did Apple's revenue and gross margin change last quarter, and why?")

    assert fake_rewriter.called
    assert fake_rewriter.requested_roles == ["slot", "financial_statement", "mda"]
    assert "dense_query:llm_rewriter" in plan.matched_rules
    assert plan.dense_query_specs[0]["text"] == (
        "latest quarterly financial performance showing year over year revenue and gross margin changes"
    )
    assert plan.dense_query_specs[1]["role"] == "financial_statement"
    assert plan.dense_query_specs[2]["role"] == "mda"
    assert any(spec["role"] == "original" for spec in plan.dense_query_specs)


def test_llm_mode_falls_back_when_dense_rewriter_specs_are_invalid() -> None:
    fake_llm = FakeLLMPlanner(
        {
            "question_type": "mixed",
            "metric_keys": ["revenue"],
            "time_scope": "latest",
            "comparison_basis": "latest_quarter_yoy",
            "comparison_candidates": ["latest_quarter_yoy"],
            "target_sections": [
                "Financial Statements",
                "Management's Discussion and Analysis",
            ],
            "forms": ["10-Q"],
        }
    )
    planner = QueryPlanner(
        settings=Settings(_env_file=None, query_planner_mode="llm"),
        llm_planner=fake_llm,
        dense_query_rewriter=FakeDenseQueryRewriter(
            [
                {"role": "slot", "text": "too short"},
                {
                    "role": "risk",
                    "text": "risk factors item 1a unrelated legal regulatory business risks",
                },
            ]
        ),
    )

    plan = planner.plan("Why did Apple's revenue change last quarter?")

    assert "dense_query:hardcoded_fallback:no_valid_specs" in plan.matched_rules
    assert any(
        "financial statement amount management discussion reasons drivers" in spec["text"]
        for spec in plan.dense_query_specs
    )


def test_llm_dense_roles_leave_room_for_original_query() -> None:
    fake_llm = FakeLLMPlanner(
        {
            "question_type": "mixed",
            "metric_keys": ["operating_cash_flow"],
            "time_scope": "latest",
            "comparison_basis": "none",
            "comparison_candidates": [],
            "target_sections": [
                "Financial Statements",
                "Cash Flows",
                "Liquidity",
                "Management's Discussion and Analysis",
                "Risk Factors",
            ],
            "forms": ["10-Q"],
        }
    )
    fake_rewriter = FakeDenseQueryRewriter(
        [
            {
                "role": "slot",
                "text": "latest filing evidence covering cash flow liquidity risks and performance drivers",
            },
            {
                "role": "financial_statement",
                "text": "financial statements showing cash flow line items and reported operating activities amounts",
            },
            {
                "role": "cash_flow",
                "text": "statements of cash flows showing operating investing and financing activities evidence",
            },
            {
                "role": "liquidity",
                "text": "liquidity and capital resources discussion of cash requirements and funding capacity",
            },
            {
                "role": "mda",
                "text": "results of operations discussion explaining cash flow and performance drivers",
            },
        ]
    )
    planner = QueryPlanner(
        settings=Settings(_env_file=None, query_planner_mode="llm"),
        llm_planner=fake_llm,
        dense_query_rewriter=fake_rewriter,
    )

    plan = planner.plan("Explain Apple's cash flow, liquidity, risks, and performance drivers.")

    assert fake_rewriter.requested_roles == [
        "slot",
        "financial_statement",
        "cash_flow",
        "liquidity",
        "mda",
    ]
    assert len(plan.dense_query_specs) == 6
    assert plan.dense_query_specs[-1]["role"] == "original"
    assert plan.dense_queries[-1] == (
        "Explain Apple's cash flow, liquidity, risks, and performance drivers."
    )


def test_legacy_rule_with_llm_fallback_mode_now_calls_llm_first() -> None:
    fake_llm = FakeLLMPlanner(
        {
            "question_type": "risk",
            "metric_keys": [],
            "time_scope": "latest",
            "comparison_basis": "none",
            "comparison_candidates": [],
            "target_sections": ["Risk Factors"],
            "forms": ["10-K"],
        }
    )
    planner = QueryPlanner(
        settings=Settings(_env_file=None, query_planner_mode="rule_with_llm_fallback"),
        llm_planner=fake_llm,
    )

    plan = planner.plan("What are Apple's latest risk factors?")

    assert fake_llm.called
    assert plan.planner_source == "llm_validated"
    assert plan.question_type == "risk"
    assert plan.target_sections == ["Risk Factors"]
    assert plan.evidence_roles == ["risk_factor_chunks"]


def test_rule_only_mode_uses_conservative_text_fallback_without_llm() -> None:
    fake_llm = FakeLLMPlanner({"question_type": "risk"})
    planner = QueryPlanner(
        settings=Settings(_env_file=None, query_planner_mode="rule_only"),
        llm_planner=fake_llm,
    )

    plan = planner.plan("Tell me about Apple.", form_type="10-Q")

    assert not fake_llm.called
    assert plan.planner_source == "fallback_validated"
    assert plan.question_type == "prose"
    assert plan.metric_keys == []
    assert plan.forms == ["10-Q"]
    assert plan.allowed_forms == ["10-Q"]
    assert not plan.needs_financial_facts
    assert "legacy_rule_only_mode" in plan.matched_rules


def test_llm_failure_falls_back_to_broad_text_retrieval() -> None:
    planner = QueryPlanner(
        settings=Settings(_env_file=None, query_planner_mode="llm"),
        llm_planner=FakeLLMPlanner(should_raise=True),
    )

    plan = planner.plan("最近营收同比变化如何？", section="Management's Discussion and Analysis")

    assert plan.planner_source == "fallback_validated"
    assert plan.question_type == "prose"
    assert plan.metric_keys == []
    assert plan.target_sections == ["Management's Discussion and Analysis"]
    assert plan.dense_queries == ["最近营收同比变化如何？"]
    assert plan.lexical_queries == ["最近营收同比变化如何？"]
    assert not plan.needs_metric_comparisons
    assert any(rule.startswith("llm_failed:") for rule in plan.matched_rules)


def test_invalid_llm_candidate_falls_back_instead_of_using_rules() -> None:
    planner = QueryPlanner(
        settings=Settings(_env_file=None, query_planner_mode="llm"),
        llm_planner=FakeLLMPlanner({"unexpected": "field"}),
    )

    plan = planner.plan("What was Apple's revenue last year?")

    assert plan.planner_source == "fallback_validated"
    assert plan.matched_rules[0] == "planner:safe_text_fallback"
    assert plan.metric_keys == []


def test_validator_filters_to_allowed_schema_values_and_preserves_request_filters() -> None:
    query = QueryNormalizer().normalize(
        "Why did gross margin change?",
        form_type="10-q",
        section="Management's Discussion and Analysis",
    )

    plan = PlanValidator().validate(
        {
            "question_type": "mixed",
            "metric_keys": ["gross_margin", "unsupported_metric"],
            "time_scope": "comparison_trend",
            "comparison_basis": "ambiguous",
            "comparison_candidates": ["latest_quarter_yoy", "bad_basis"],
            "target_sections": ["Financial Statements", "Bad Section"],
            "forms": ["8-K"],
        },
        query,
    )

    assert plan.metric_keys == ["gross_margin"]
    assert plan.comparison_candidates == ["latest_quarter_yoy"]
    assert plan.default_comparison_basis == "latest_quarter_yoy"
    assert plan.target_sections == [
        "Financial Statements",
        "Management's Discussion and Analysis",
    ]
    assert plan.forms == ["10-Q"]
    assert plan.allowed_forms == ["10-Q"]
    assert plan.preferred_forms == ["10-Q"]
    assert plan.evidence_roles == [
        "metric_comparisons",
        "primary_financial_statement_chunks",
        "mda_explanation_chunks",
    ]


def test_validator_ignores_invalid_request_section_filter() -> None:
    query = QueryNormalizer().normalize("Why did gross margin change?", section="Bad Section")

    plan = PlanValidator().validate(
        {
            "question_type": "mixed",
            "metric_keys": ["gross_margin"],
            "time_scope": "comparison_trend",
            "comparison_basis": "ambiguous",
            "comparison_candidates": ["latest_quarter_yoy"],
            "target_sections": ["Financial Statements"],
            "forms": [],
        },
        query,
    )

    assert plan.target_sections == ["Financial Statements"]


def test_safe_text_fallback_ignores_invalid_request_section_filter() -> None:
    planner = QueryPlanner(
        settings=Settings(_env_file=None, query_planner_mode="llm"),
        llm_planner=FakeLLMPlanner(should_raise=True),
    )

    plan = planner.plan("Tell me about Apple.", section="Bad Section")

    assert plan.target_sections == []


def test_validator_repairs_broad_performance_plan_for_retrieval() -> None:
    query = QueryNormalizer().normalize("How did Apple do last quarter?")

    plan = PlanValidator().validate(
        {
            "question_type": "performance_overview",
            "metric_keys": [],
            "time_scope": "latest",
            "comparison_basis": "latest_quarter_yoy",
            "comparison_candidates": [],
            "target_sections": [
                "Financial Statements",
                "Management's Discussion and Analysis",
            ],
            "forms": ["10-Q"],
        },
        query,
    )

    assert plan.metric_keys == [
        "revenue",
        "gross_margin",
        "operating_income",
        "net_income",
    ]
    assert plan.needs_financial_facts
    assert plan.needs_text_chunks
    assert plan.needs_metric_comparisons
    assert plan.forms == []
    assert plan.allowed_forms == ["10-Q", "10-K"]
    assert plan.preferred_forms == ["10-Q"]
    assert any(
        "latest quarter financial performance results of operations" in spec["text"]
        for spec in plan.dense_query_specs
    )
    assert any(spec["role"] == "original" for spec in plan.dense_query_specs)
    assert "primary_financial_statement_chunks" in plan.evidence_roles
    assert "mda_explanation_chunks" in plan.evidence_roles
    assert "segment_or_product_breakdown_chunks" in plan.evidence_roles


def test_validator_infers_quarter_duration_and_soft_form_scope() -> None:
    query = QueryNormalizer().normalize("How much revenue did Apple report last quarter?")

    plan = PlanValidator().validate(
        {
            "question_type": "metric",
            "target_sections": ["Financial Statements"],
            "metric_keys": ["revenue"],
            "time_scope": "latest",
            "comparison_basis": "none",
            "comparison_candidates": [],
            "forms": ["10-Q"],
            "preferred_forms": ["10-Q"],
        },
        query,
    )

    assert plan.period_kind == "quarter"
    assert plan.target_period == "latest"
    assert plan.duration_class == "quarter"
    assert plan.forms == []
    assert plan.allowed_forms == ["10-Q", "10-K"]
    assert plan.preferred_forms == ["10-Q"]
    assert plan.lexical_query_specs[0]["role"] == "primary_financial_statement"
    assert '"three months ended"' in plan.lexical_query_specs[0]["queries"]
    assert '"three months ended" "net sales"' in plan.lexical_query_specs[0]["queries"]
    assert any(
        spec["role"] == "mda_explanation" and spec["weight"] == 0.35
        for spec in plan.lexical_query_specs
    )


def test_validator_keeps_metric_text_chunks_when_sections_are_missing() -> None:
    query = QueryNormalizer().normalize("How much revenue did Apple report last quarter?")

    plan = PlanValidator().validate(
        {
            "question_type": "metric",
            "target_sections": [],
            "metric_keys": ["revenue"],
            "time_scope": "latest",
            "comparison_basis": "none",
            "comparison_candidates": [],
        },
        query,
    )

    assert plan.needs_text_chunks
    assert "primary_financial_statement_chunks" in plan.evidence_roles


def test_dense_rewriter_payload_includes_period_slots() -> None:
    query = QueryNormalizer().normalize("How much revenue did Apple report last quarter?")
    plan = PlanValidator().validate(
        {
            "question_type": "metric",
            "target_sections": ["Financial Statements"],
            "metric_keys": ["revenue"],
            "time_scope": "latest",
            "period_kind": "quarter",
            "target_period": "latest",
            "duration_class": "quarter",
            "comparison_basis": "none",
            "comparison_candidates": [],
        },
        query,
    )

    payload = _llm_dense_query_rewriter_payload(
        question=query.original,
        plan=plan,
        requested_roles=["slot", "financial_statement"],
    )

    assert payload["validated_slots"]["period_kind"] == "quarter"
    assert payload["validated_slots"]["target_period"] == "latest"
    assert payload["validated_slots"]["duration_class"] == "quarter"
    assert payload["allowed_period_terms"] == [
        "latest quarter",
        "three months ended",
        "quarterly",
    ]


def test_validator_accepts_instant_duration_class() -> None:
    query = QueryNormalizer().normalize("How much cash did Apple have as of last quarter?")

    plan = PlanValidator().validate(
        {
            "question_type": "metric",
            "target_sections": ["Financial Statements"],
            "metric_keys": ["revenue"],
            "time_scope": "latest",
            "period_kind": "instant",
            "target_period": "latest",
            "duration_class": "instant",
            "comparison_basis": "none",
            "comparison_candidates": [],
        },
        query,
    )

    assert plan.period_kind == "instant"
    assert plan.duration_class == "instant"
    assert plan.allowed_forms == ["10-Q", "10-K"]


def test_validator_keeps_explicit_form_as_hard_filter() -> None:
    query = QueryNormalizer().normalize("How much revenue was in Apple's latest 10-Q?")

    plan = PlanValidator().validate(
        {
            "question_type": "metric",
            "target_sections": ["Financial Statements"],
            "metric_keys": ["revenue"],
            "time_scope": "latest",
            "comparison_basis": "none",
            "comparison_candidates": [],
            "forms": ["10-Q"],
            "preferred_forms": ["10-Q"],
        },
        query,
    )

    assert plan.forms == ["10-Q"]
    assert plan.allowed_forms == ["10-Q"]


def test_validator_does_not_treat_implicit_forms_as_hard_filters() -> None:
    query = QueryNormalizer().normalize("How much cash did Apple generate?")

    plan = PlanValidator().validate(
        {
            "question_type": "metric",
            "target_sections": ["Cash Flows"],
            "metric_keys": ["operating_cash_flow"],
            "time_scope": "latest",
            "comparison_basis": "none",
            "comparison_candidates": [],
            "forms": ["10-Q"],
            "preferred_forms": ["10-Q"],
        },
        query,
    )

    assert plan.forms == []
    assert plan.duration_class == "quarter"
    assert plan.allowed_forms == ["10-Q", "10-K"]
    assert plan.preferred_forms == ["10-Q"]


def test_validator_enriches_cash_flow_queries_with_sec_filing_context() -> None:
    query = QueryNormalizer().normalize("How much cash did Apple generate?")

    plan = PlanValidator().validate(
        {
            "question_type": "metric",
            "target_sections": ["Cash Flows"],
            "metric_keys": ["operating_cash_flow"],
            "time_scope": "latest",
            "comparison_basis": "none",
            "comparison_candidates": [],
            "forms": ["10-K", "10-Q"],
        },
        query,
    )

    dense_text = " ".join(plan.dense_queries)
    assert "statement of cash flows" in dense_text
    assert "net cash provided by operating activities" in dense_text
    assert "cash provided by operating activities" in dense_text
    assert any(len(dense_query.split()) >= 10 for dense_query in plan.dense_queries)
    assert '"net cash provided by operating activities"' in plan.lexical_queries
    assert '"statements of cash flows"' in plan.lexical_queries
    assert '"operating activities"' in plan.lexical_queries
    assert {
        spec["text"]
        for spec in plan.dense_query_specs
    }.issuperset(set(plan.dense_queries))


def test_lexical_queries_balance_metrics_sections_and_comparison_terms() -> None:
    query = QueryNormalizer().normalize(
        "How did revenue and gross margin change, and why?"
    )

    plan = PlanValidator().validate(
        {
            "question_type": "mixed",
            "target_sections": [
                "Financial Statements",
                "Management's Discussion and Analysis",
            ],
            "metric_keys": ["revenue", "gross_margin"],
            "time_scope": "latest",
            "comparison_basis": "latest_quarter_yoy",
            "comparison_candidates": ["latest_quarter_yoy"],
            "forms": ["10-Q"],
        },
        query,
    )

    assert '"results of operations"' in plan.lexical_queries
    assert '"three months ended"' in plan.lexical_queries
    assert '"compared to"' in plan.lexical_queries
    assert '"prior year"' in plan.lexical_queries
    assert '"three months ended" "net sales"' in plan.lexical_queries
    assert '"gross margin"' in plan.lexical_queries
    assert '"six months ended"' not in plan.lexical_queries
    assert '"year ended"' not in plan.lexical_queries


def test_lexical_comparison_terms_follow_ytd_basis() -> None:
    query = QueryNormalizer().normalize("How did revenue change year to date?")

    plan = PlanValidator().validate(
        {
            "question_type": "trend",
            "target_sections": ["Financial Statements"],
            "metric_keys": ["revenue"],
            "time_scope": "comparison_trend",
            "comparison_basis": "latest_ytd_yoy",
            "comparison_candidates": ["latest_ytd_yoy"],
            "forms": ["10-Q"],
        },
        query,
    )

    assert '"six months ended"' in plan.lexical_queries
    assert '"nine months ended"' in plan.lexical_queries
    assert '"three months ended"' not in plan.lexical_queries
    assert '"year ended"' not in plan.lexical_queries


def test_validator_repairs_ambiguous_metric_comparison_candidates() -> None:
    query = QueryNormalizer().normalize("Did Apple's cash flow improve?")

    plan = PlanValidator().validate(
        {
            "question_type": "performance_judgment",
            "target_sections": ["Cash Flows", "Liquidity"],
            "metric_keys": ["operating_cash_flow"],
            "time_scope": "latest",
            "comparison_basis": "ambiguous",
            "comparison_candidates": [],
            "forms": [],
        },
        query,
    )

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


def test_llm_planner_prompt_lists_schema_enums_and_retrieval_fields() -> None:
    prompt = _llm_planner_system_prompt()

    assert "Allowed question_type values" in prompt
    assert "- performance_overview" in prompt
    assert "- liquidity" in prompt
    assert "- filing_summary" in prompt
    assert "- revenue" in prompt
    assert "- latest_quarter_yoy" in prompt
    assert "- Financial Statements" in prompt
    assert "- 10-Q" in prompt
    assert "Do not output dense_queries" in prompt
    assert "Prefer semantic understanding over keyword matching" in prompt
    assert "Few-shot examples" in prompt
    assert "Input:\nHow did Apple do last quarter?" in prompt
    assert '"metric_keys": ["revenue", "gross_margin", "operating_income", "net_income"]' in prompt
    assert "How did Apple's revenue and gross margin change last quarter, and why?" in prompt
    assert "The question is expected to be\nEnglish." in prompt
    assert "Input:\nHow much cash did Apple generate?" in prompt
    assert '"metric_keys": ["operating_cash_flow"]' in prompt


def test_llm_dense_query_rewriter_prompt_explains_roles_and_examples() -> None:
    prompt = _llm_dense_query_rewriter_system_prompt()

    assert "For slot, write one broad semantic query" in prompt
    assert "latest quarterly financial performance showing year over year revenue" in prompt
    assert "consolidated statements of operations showing three months ended" in prompt
    assert "results of operations discussion explaining revenue and gross margin drivers" in prompt
    assert "Generate exactly one query for each requested role" in prompt
    assert "Do not invent numbers" in prompt


def test_llm_query_planner_rejects_unknown_fields_from_raw_response() -> None:
    assert "unexpected" not in LLMQueryPlanner.allowed_fields
    assert "dense_queries" not in LLMQueryPlanner.allowed_fields
    assert "lexical_queries" not in LLMQueryPlanner.allowed_fields
    # Single-call planning: dense_query_specs may now arrive with the slots.
    assert "dense_query_specs" in LLMQueryPlanner.allowed_fields


def test_llm_mode_uses_inline_dense_specs_without_rewriter_call() -> None:
    fake_llm = FakeLLMPlanner(
        {
            "question_type": "mixed",
            "metric_keys": ["revenue", "gross_margin"],
            "time_scope": "latest",
            "comparison_basis": "latest_quarter_yoy",
            "comparison_candidates": ["latest_quarter_yoy"],
            "target_sections": [
                "Financial Statements",
                "Management's Discussion and Analysis",
            ],
            "forms": ["10-Q"],
            "dense_query_specs": [
                {
                    "role": "slot",
                    "text": "latest quarterly financial performance showing year over year revenue and gross margin changes",
                },
                {
                    "role": "financial_statement",
                    "text": "consolidated statements of operations showing three months ended net sales gross margin and cost of sales",
                },
                {
                    "role": "mda",
                    "text": "results of operations discussion explaining revenue and gross margin drivers compared to the prior year quarter",
                },
            ],
        }
    )
    fake_rewriter = FakeDenseQueryRewriter([])
    planner = QueryPlanner(
        settings=Settings(_env_file=None, query_planner_mode="llm"),
        llm_planner=fake_llm,
        dense_query_rewriter=fake_rewriter,
    )

    plan = planner.plan(
        "How did Apple's revenue and gross margin change last quarter, and why?"
    )

    assert not fake_rewriter.called
    assert "dense_query:planner_single_call" in plan.matched_rules
    roles = [spec["role"] for spec in plan.dense_query_specs]
    assert "slot" in roles
    assert "financial_statement" in roles
    assert "mda" in roles
    assert "original" in roles


def test_llm_mode_falls_back_to_rewriter_when_inline_specs_invalid() -> None:
    fake_llm = FakeLLMPlanner(
        {
            "question_type": "mixed",
            "metric_keys": ["revenue", "gross_margin"],
            "time_scope": "latest",
            "comparison_basis": "latest_quarter_yoy",
            "comparison_candidates": ["latest_quarter_yoy"],
            "target_sections": [
                "Financial Statements",
                "Management's Discussion and Analysis",
            ],
            "forms": ["10-Q"],
            "dense_query_specs": [
                {"role": "unknown_role", "text": "too short"},
            ],
        }
    )
    fake_rewriter = FakeDenseQueryRewriter(
        [
            {
                "role": "slot",
                "text": "latest quarterly financial performance showing year over year revenue and gross margin changes",
            },
            {
                "role": "mda",
                "text": "results of operations discussion explaining revenue and gross margin drivers compared to the prior year quarter",
            },
        ]
    )
    planner = QueryPlanner(
        settings=Settings(_env_file=None, query_planner_mode="llm"),
        llm_planner=fake_llm,
        dense_query_rewriter=fake_rewriter,
    )

    plan = planner.plan(
        "How did Apple's revenue and gross margin change last quarter, and why?"
    )

    assert fake_rewriter.called
    assert "dense_query:llm_rewriter" in plan.matched_rules

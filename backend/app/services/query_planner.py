from __future__ import annotations

from dataclasses import asdict, dataclass, field, replace
import json
import re
from typing import Any, Protocol

from app.core import Settings, get_settings
from app.services.metric_profiles import METRIC_RETRIEVAL_PROFILES, get_metric_profile
from app.services.xbrl_metrics import METRIC_LABELS


RISK_TERMS = ("risk", "risks", "risk factors", "item 1a", "风险")
MDA_TERMS = (
    "md&a",
    "management discussion",
    "management's discussion",
    "management discussion and analysis",
    "管理层讨论",
)
EXPLANATION_TERMS = (
    "why",
    "reason",
    "reasons",
    "driver",
    "drivers",
    "drive",
    "drives",
    "drove",
    "driven",
    "cause",
    "causes",
    "caused",
    "causing",
    "explain",
    "explains",
    "explained",
    "explaining",
    "behind",
    "factor",
    "factors",
    "原因",
)
SUMMARY_TERMS = (
    "summarize",
    "summary",
    "recap",
    "key takeaways",
    "takeaways",
    "highlights",
    "overview",
    "brief",
    "briefly",
    "walk me through",
    "概括",
    "总结",
    "摘要",
    "要点",
)
FILING_SUMMARY_CONTEXT_TERMS = (
    "filing",
    "filings",
    "report",
    "reported",
    "reports",
    "10-q",
    "10-k",
    "quarterly report",
    "annual report",
    "earnings report",
    "earnings release",
    "latest results",
    "recent results",
    "financial results",
    "latest quarter",
    "recent quarter",
    "latest filing",
    "recent filing",
    "财报",
    "季报",
    "年报",
    "业绩",
)
FILING_REPORT_TERMS = (
    "filing",
    "filings",
    "10-q",
    "10-k",
    "quarterly report",
    "annual report",
    "earnings report",
    "earnings release",
    "financial report",
    "财报",
    "季报",
    "年报",
)
BROAD_EARNINGS_REPORT_TERMS = (
    "earnings report",
    "earnings release",
    "latest results",
    "recent results",
    "financial results",
    "财报",
    "业绩",
)
LIQUIDITY_TERMS = (
    "liquidity",
    "liquidity and capital resources",
    "capital resources",
    "cash",
    "cash position",
    "cash balance",
    "cash balances",
    "cash flow",
    "cash flows",
    "cash generation",
    "generate cash",
    "generating cash",
    "generated cash",
    "enough cash",
    "working capital",
    "fund operations",
    "funding",
    "流动性",
    "现金",
    "现金流",
    "资金",
)
LIQUIDITY_NEGATIVE_TERMS = (
    "cash burn",
    "burn rate",
    "dividend",
    "dividends",
    "buyback",
    "buybacks",
    "repurchase",
    "repurchases",
    "stock price",
    "share price",
)
LIQUIDITY_SECTION_TERMS = (
    "liquidity",
    "liquidity and capital resources",
    "capital resources",
    "cash position",
    "cash balance",
    "cash balances",
    "enough cash",
    "working capital",
    "fund operations",
    "funding",
    "流动性",
    "现金",
    "资金",
)
PERFORMANCE_TERMS = (
    "how did",
    "how is",
    "how are",
    "how was",
    "how were",
    "perform",
    "performs",
    "performed",
    "performing",
    "performance",
    "results",
    "financial results",
    "business results",
    "operating results",
    "results of operations",
    "表现",
    "怎么样",
    "如何",
    "做得",
)
CURRENT_PERFORMANCE_TERMS = (
    "how is",
    "how are",
    "how's",
    "how is it doing",
    "how are they doing",
    "how is the company doing",
    "doing now",
    "doing recently",
    "doing lately",
    "公司现在怎么样",
    "最近怎么样",
)
JUDGMENT_TERMS = (
    "strong",
    "strength",
    "weak",
    "weakness",
    "good",
    "well",
    "bad",
    "solid",
    "robust",
    "healthy",
    "soft",
    "sluggish",
    "poor",
    "impressive",
    "disappointing",
    "resilient",
    "outperform",
    "outperformed",
    "underperform",
    "underperformed",
    "improve",
    "improved",
    "improves",
    "improving",
    "get better",
    "got better",
    "gotten better",
    "getting better",
    "better",
    "worsen",
    "worsened",
    "worsens",
    "worsening",
    "get worse",
    "got worse",
    "gotten worse",
    "getting worse",
    "deteriorate",
    "deteriorated",
    "deteriorates",
    "deteriorating",
    "好",
    "强",
    "强劲",
    "稳健",
    "疲软",
    "弱",
    "差",
    "不佳",
)
PERFORMANCE_PERSISTENCE_TERMS = (
    "still",
    "remain",
    "remains",
    "remained",
    "remaining",
    "continue",
    "continues",
    "continued",
    "continuing",
    "maintain",
    "maintains",
    "maintained",
    "maintaining",
    "sustain",
    "sustains",
    "sustained",
    "sustaining",
    "stay",
    "stays",
    "stayed",
    "staying",
    "hold up",
    "holds up",
    "held up",
    "holding up",
    "keep up",
    "keeps up",
    "kept up",
    "keeping up",
    "仍然",
    "依然",
    "继续",
    "保持",
    "维持",
)
COMPARATIVE_METRIC_MODIFIER_TERMS = (
    "more",
    "less",
    "higher",
    "lower",
    "up",
    "down",
    "increased",
    "decreased",
    "stronger",
    "weaker",
    "better",
    "worse",
)
LATEST_TERMS = (
    "latest",
    "most recent",
    "recent",
    "recently",
    "lately",
    "now",
    "currently",
    "right now",
    "at the moment",
    "latest quarter",
    "most recent quarter",
    "recent quarter",
    "last quarter",
    "最近一个季度",
    "上一季度",
    "最新",
    "最近",
)
TREND_TERMS = (
    "growth",
    "grow",
    "grows",
    "grew",
    "grown",
    "growing",
    "expand",
    "expands",
    "expanded",
    "expanding",
    "expansion",
    "rise",
    "rises",
    "rose",
    "risen",
    "rising",
    "fall",
    "falls",
    "fell",
    "fallen",
    "falling",
    "more than",
    "less than",
    "higher than",
    "lower than",
    "up from",
    "down from",
    "up year",
    "down year",
    "better than",
    "worse than",
    "improve",
    "improved",
    "improves",
    "improving",
    "get better",
    "got better",
    "gotten better",
    "getting better",
    "better",
    "worsen",
    "worsened",
    "worsens",
    "worsening",
    "get worse",
    "got worse",
    "gotten worse",
    "getting worse",
    "deteriorate",
    "deteriorated",
    "deteriorates",
    "deteriorating",
    "increase",
    "increased",
    "increasing",
    "decrease",
    "decreased",
    "decreasing",
    "decline",
    "declined",
    "declining",
    "change",
    "changed",
    "changing",
    "accelerate",
    "accelerated",
    "accelerating",
    "slow",
    "slowed",
    "slowing",
    "yoy",
    "year over year",
    "year-over-year",
    "compared with",
    "compared to",
    "compare",
    "versus",
    "vs.",
    "vs",
    "trend",
    "变化",
    "增长",
    "增加",
    "提升",
    "改善",
    "改进",
    "好转",
    "下降",
    "减少",
    "下滑",
    "恶化",
    "同比",
    "相比",
    "高于",
    "低于",
    "多于",
    "少于",
)
ACCELERATION_TERMS = (
    "accelerate",
    "accelerated",
    "accelerating",
    "acceleration",
    "faster",
    "speed up",
    "speeding up",
    "decelerate",
    "decelerated",
    "decelerating",
    "deceleration",
    "slower",
    "slowdown",
    "slow down",
    "slowed",
    "slowing",
    "momentum",
    "加速",
    "放缓",
    "减速",
    "动能",
)

QUARTERLY_BASIS_TERMS = (
    "quarter",
    "quarterly",
    "latest quarter",
    "three months",
    "q1",
    "q2",
    "q3",
    "q4",
    "季度",
)
YTD_BASIS_TERMS = (
    "year-to-date",
    "year to date",
    "ytd",
    "six months",
    "nine months",
    "六个月",
    "九个月",
    "年初至今",
)
FY_BASIS_TERMS = (
    "annual",
    "annually",
    "fiscal year",
    "full year",
    "year ended",
    "last year",
    "prior year",
    "previous year",
    "year ago",
    "year earlier",
    "fy",
    "全年",
    "财年",
    "年度",
    "去年",
    "上一年",
    "上年",
)

SALES_ACTIVITY_TERMS = (
    "sell",
    "sells",
    "selling",
    "sold",
    "sales",
    "卖",
    "销售",
)
SALES_ACTIVITY_COMPARISON_TERMS = (
    "sell more",
    "selling more",
    "sold more",
    "sell less",
    "selling less",
    "sold less",
    "sales higher",
    "sales lower",
    "sales up",
    "sales down",
    "more sales",
    "less sales",
    "higher sales",
    "lower sales",
    "销售增长",
    "销售下降",
    "卖得更多",
    "卖得更少",
)
SALES_ACTIVITY_CONTEXT_TERMS = (
    *TREND_TERMS,
    *LATEST_TERMS,
    *PERFORMANCE_TERMS,
    *SALES_ACTIVITY_COMPARISON_TERMS,
)

DEFAULT_COMPANY_GROWTH_METRICS = ("revenue", "operating_income", "net_income")
DEFAULT_COMPANY_CHANGE_METRICS = (
    "revenue",
    "gross_margin",
    "operating_income",
    "net_income",
)
DEFAULT_PROFITABILITY_METRICS = ("operating_income", "net_income")
DEFAULT_MARGIN_METRICS = ("gross_margin", "operating_margin", "net_margin")
DEFAULT_LIQUIDITY_METRICS = ("operating_cash_flow", "free_cash_flow")
DEFAULT_SUMMARY_METRICS = (
    "revenue",
    "gross_margin",
    "operating_income",
    "net_income",
    "operating_cash_flow",
    "free_cash_flow",
)
BROAD_COMPANY_GROWTH_TERMS = (
    "growth",
    "grow",
    "grows",
    "grew",
    "grown",
    "growing",
    "expand",
    "expands",
    "expanded",
    "expanding",
    "improve",
    "improved",
    "improves",
    "improving",
    "get better",
    "got better",
    "gotten better",
    "getting better",
    "增长",
    "成长",
    "扩张",
    "改善",
)
PROFITABILITY_TERMS = (
    "profitability",
    "profitable",
    "profit",
    "profits",
    "earnings",
    "盈利",
    "利润",
)
EARNINGS_ACTIVITY_TERMS = (
    "make money",
    "makes money",
    "made money",
    "making money",
    "earn money",
    "earns money",
    "earned money",
    "earning money",
)
MARGIN_JUDGMENT_TERMS = (
    "margin",
    "margins",
    "利润率",
    "毛利率",
)
BROAD_COMPANY_GROWTH_EXCLUDED_SUBJECTS = (
    "debt",
    "liability",
    "liabilities",
    "headcount",
    "employee",
    "employees",
    "user",
    "users",
    "subscriber",
    "subscribers",
    "inventory",
    "inventories",
    "expense",
    "expenses",
    "cost",
    "costs",
    "margin",
    "cash",
    "cash flow",
    "cash burn",
    "burn",
    "capex",
    "capital expenditure",
    "dividend",
    "buyback",
    "repurchase",
    "stock",
    "share count",
    "share price",
    "stock price",
    "market cap",
    "market capitalization",
    "valuation",
    "multiple",
    "goodwill",
    "dollar",
    "currency",
    "foreign exchange",
    "fx",
    "accountant",
    "accountants",
    "auditor",
    "audit",
    "accounting policy",
    "controls",
    "procedures",
    "leadership",
    "management team",
)
NON_FILING_EXPLANATION_SUBJECT_TERMS = (
    "stock",
    "share price",
    "stock price",
    "market cap",
    "market capitalization",
    "valuation",
    "multiple",
)
VAGUE_COMPANY_CHANGE_TERMS = (
    "what changed",
    "what has changed",
    "what's changed",
    "what is different",
    "what was different",
    "changed",
    "changes",
    "change",
    "different",
    "difference",
    "moved",
    "shifted",
    "变化",
    "变了",
    "有什么变化",
)
BROAD_PERFORMANCE_DRIVER_SUBJECT_TERMS = (
    "performance",
    "financial performance",
    "business performance",
    "results",
    "financial results",
    "business results",
    "operating results",
    "results of operations",
    "表现",
)
COMPARISON_CONTEXT_TERMS = (
    "compared with",
    "compared to",
    "compare with",
    "compare to",
    "versus",
    "vs.",
    "vs",
    "yoy",
    "year over year",
    "year-over-year",
    "last year",
    "prior year",
    "previous year",
    "year ago",
    "year earlier",
    "same period",
    "同比",
    "相比",
    "去年",
    "上一年",
    "上年",
)
COMPANY_GROWTH_LEXICAL_QUERIES = (
    '"net sales"',
    '"total net sales"',
    '"net sales increased"',
    '"revenue growth"',
    '"operating income"',
    '"income from operations"',
    '"net income"',
    '"net earnings"',
    '"products and services performance"',
    '"segment operating performance"',
    '"net sales" "compared to"',
    '"operating income" "compared to"',
    '"net income" "compared to"',
    '"year-over-year" "net sales"',
)
COMPANY_CHANGE_LEXICAL_QUERIES = (
    '"net sales" "change"',
    '"net sales" "compared to"',
    '"total net sales" "change"',
    '"products and services performance"',
    '"segment operating performance"',
    '"net sales by category"',
    '"net sales by reportable segment"',
    '"operating income" "change"',
    '"operating income" "compared to"',
    '"net income" "change"',
    '"net income" "compared to"',
    '"gross margin" "compared to"',
    '"gross margin percentage"',
    '"gross margin percentage increased"',
    '"gross margin percentage decreased"',
    '"primarily due to"',
    '"higher net sales"',
    '"lower net sales"',
    '"cost of sales"',
    '"year ended" "change"',
    '"year-over-year"',
)
COMPANY_QUARTER_PERFORMANCE_LEXICAL_QUERIES = (
    '"three months ended" "net sales"',
    '"condensed consolidated statements of operations"',
    '"products and services performance"',
    '"segment operating performance"',
    '"net sales by category"',
    '"net sales by reportable segment"',
    '"gross margin percentage"',
    '"operating income"',
    '"net income"',
    '"same quarter"',
    '"compared to the same period"',
    '"primarily due to"',
    '"higher net sales"',
    '"lower net sales"',
)
PERFORMANCE_JUDGMENT_LEXICAL_QUERIES = (
    '"compared to"',
    '"same quarter"',
    '"year-over-year"',
    '"increased during"',
    '"decreased during"',
    '"three months ended"',
)
GROWTH_ACCELERATION_LEXICAL_QUERIES = (
    '"growth accelerated"',
    '"growth decelerated"',
    '"growth slowed"',
    '"growth speeding up"',
    '"growth momentum"',
    '"year-over-year" "growth"',
    '"compared to the same quarter"',
    '"same quarter"',
    '"three months ended"',
    '"net sales" "year-over-year"',
)
LIQUIDITY_LEXICAL_QUERIES = (
    '"liquidity and capital resources"',
    '"liquidity"',
    '"capital resources"',
    '"cash and cash equivalents"',
    '"cash flows from operating activities"',
    '"net cash provided by operating activities"',
    '"statements of cash flows"',
    '"working capital"',
)
FILING_SUMMARY_LEXICAL_QUERIES = (
    '"management discussion and analysis"',
    '"results of operations"',
    '"condensed consolidated statements of operations"',
    '"consolidated statements of operations"',
    '"statements of cash flows"',
    '"liquidity and capital resources"',
    '"risk factors"',
    '"net sales"',
    '"gross margin"',
    '"operating income"',
    '"net income"',
)
MAX_DENSE_QUERY_WORDS = 45
DENSE_INTENT_PHRASES = {
    "performance_overview": (
        "financial performance results business performance"
    ),
    "trend": "growth trend increased decreased compared to prior period",
    "broad_comparison": (
        "financial performance changes results of operations compared to prior period"
    ),
    "performance_judgment": (
        "performance strength weakness improvement deterioration financial results"
    ),
    "mixed": "financial results drivers reasons primarily due to higher lower",
    "metric": "financial metric value reported amount",
    "risk": "risk factors item 1a business operational financial regulatory risks",
    "liquidity": (
        "liquidity capital resources cash position operating cash flow free cash flow"
    ),
    "filing_summary": (
        "filing summary earnings report key takeaways financial results business overview"
    ),
    "management_discussion": "management discussion analysis results of operations",
    "growth_acceleration": (
        "growth acceleration deceleration momentum faster slower year-over-year"
    ),
}
DENSE_SECTION_PHRASES = {
    "Financial Statements": "financial statements statements of operations",
    "Management's Discussion and Analysis": (
        "management discussion analysis results of operations"
    ),
    "Risk Factors": "risk factors item 1a",
    "Liquidity": "liquidity capital resources cash flows",
    "Cash Flows": "statements of cash flows operating activities",
}
DENSE_COMPARISON_PHRASES = {
    "latest_quarter_yoy": "year-over-year compared to same quarter prior year",
    "previous_quarter_yoy": "previous quarter year-over-year growth",
    "latest_ytd_yoy": "year-over-year compared to same period prior year",
    "previous_ytd_yoy": "previous year-to-date year-over-year growth",
    "latest_fy_yoy": "year-over-year compared to prior fiscal year",
    "previous_fy_yoy": "previous fiscal year year-over-year growth",
    "ambiguous": "compared to prior period year-over-year change",
}
DENSE_TIME_PHRASES = {
    "latest_quarter_yoy": "latest quarter three months ended",
    "previous_quarter_yoy": "previous quarter three months ended",
    "latest_ytd_yoy": "latest year-to-date six months nine months ended",
    "previous_ytd_yoy": "previous year-to-date six months nine months ended",
    "latest_fy_yoy": "latest fiscal year year ended annual",
    "previous_fy_yoy": "previous fiscal year year ended annual",
    "ambiguous": "year-over-year quarterly year-to-date annual trend",
}

METRIC_TERMS: dict[str, tuple[str, ...]] = {
    key: profile.aliases for key, profile in METRIC_RETRIEVAL_PROFILES.items()
}

VALID_FORMS = {"10-K", "10-Q", "8-K"}
VALID_QUESTION_TYPES = {
    "risk",
    "liquidity",
    "filing_summary",
    "management_discussion",
    "growth_acceleration",
    "broad_comparison",
    "performance_overview",
    "performance_judgment",
    "mixed",
    "trend",
    "metric",
    "comparison",
    "prose",
}
VALID_TIME_SCOPES = {"latest", "comparison_trend", "unspecified"}
VALID_TARGET_SECTIONS = {
    "Financial Statements",
    "Management's Discussion and Analysis",
    "Risk Factors",
    "Liquidity",
    "Cash Flows",
}
VALID_COMPARISON_BASES = {
    "none",
    "ambiguous",
    "latest_quarter_yoy",
    "previous_quarter_yoy",
    "latest_ytd_yoy",
    "previous_ytd_yoy",
    "latest_fy_yoy",
    "previous_fy_yoy",
}


@dataclass(frozen=True)
class RetrievalPlan:
    question_type: str
    target_sections: list[str]
    metric_keys: list[str]
    time_scope: str
    comparison_basis: str
    comparison_candidates: list[str]
    default_comparison_basis: str | None
    ambiguities: list[str]
    forms: list[str]
    dense_queries: list[str]
    lexical_queries: list[str]
    rule_confidence: float
    matched_rules: list[str]
    preferred_forms: list[str] = field(default_factory=list)
    dense_query_specs: list[dict[str, Any]] = field(default_factory=list)
    planner_source: str = "rule_validated"
    confidence_breakdown: dict[str, float] = field(default_factory=dict)
    needs_financial_facts: bool = True
    needs_text_chunks: bool = True
    needs_metric_comparisons: bool = True
    evidence_roles: list[str] = field(default_factory=list)
    requires_llm_fallback_reason: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class NormalizedQuery:
    original: str
    normalized: str
    form_type: str | None
    section: str | None


@dataclass(frozen=True)
class IntentResult:
    question_type: str
    target_sections: list[str]
    lexical_queries: list[str]
    matched_rules: list[str]
    confidence: float
    has_explanation_intent: bool
    has_performance_intent: bool
    has_judgment_intent: bool
    has_acceleration_intent: bool
    has_liquidity_intent: bool = False
    has_filing_summary_intent: bool = False


@dataclass(frozen=True)
class MetricResolution:
    metric_keys: list[str]
    matched_rules: list[str]
    confidence: float
    broad_company_growth: bool = False
    broad_company_change: bool = False
    broad_company_performance: bool = False
    unsupported_subject: bool = False


@dataclass(frozen=True)
class TimeResolution:
    time_scope: str
    comparison_basis: str
    comparison_candidates: list[str]
    default_comparison_basis: str | None
    ambiguities: list[str]
    forms: list[str]
    matched_rules: list[str]
    confidence: float
    has_conflict: bool = False


@dataclass(frozen=True)
class RetrievalStrategy:
    question_type: str
    target_sections: list[str]
    forms: list[str]
    matched_rules: list[str]
    confidence: float
    needs_financial_facts: bool
    needs_text_chunks: bool
    needs_metric_comparisons: bool
    evidence_roles: list[str]


@dataclass(frozen=True)
class ExpansionResult:
    dense_queries: list[str]
    dense_query_specs: list[dict[str, Any]]
    lexical_queries: list[str]


class LLMPlanner(Protocol):
    def plan_candidate(self, question: str) -> dict[str, Any]:
        """Return a raw JSON-compatible candidate plan."""


class QueryNormalizer:
    def normalize(
        self,
        question: str,
        *,
        form_type: str | None = None,
        section: str | None = None,
    ) -> NormalizedQuery:
        normalized = " ".join(
            question.lower()
            .replace("’", "'")
            .replace("‘", "'")
            .replace("“", '"')
            .replace("”", '"')
            .split()
        )
        return NormalizedQuery(
            original=question,
            normalized=normalized,
            form_type=form_type.strip().upper() if form_type and form_type.strip() else None,
            section=section.strip() if section and section.strip() else None,
        )


class IntentParser:
    def parse(self, query: NormalizedQuery) -> IntentResult:
        normalized = query.normalized
        matched_rules: list[str] = []
        target_sections: list[str] = []
        lexical_queries: list[str] = []

        if _contains_any(normalized, RISK_TERMS):
            matched_rules.append("section:risk_factors")
            target_sections.append("Risk Factors")
            lexical_queries.append("risk factors")

        has_liquidity_intent = _is_liquidity_question(normalized)
        if has_liquidity_intent:
            matched_rules.append("intent:liquidity")
            lexical_queries.append("liquidity and capital resources")
            if _contains_any(normalized, LIQUIDITY_SECTION_TERMS):
                target_sections.extend(
                    [
                        "Liquidity",
                        "Cash Flows",
                        "Management's Discussion and Analysis",
                    ]
                )

        has_filing_summary_intent = _is_filing_summary_question(normalized)
        if has_filing_summary_intent:
            matched_rules.append("intent:filing_summary")
            lexical_queries.append("management discussion and analysis")

        has_explanation_intent = _contains_any(normalized, EXPLANATION_TERMS)
        has_current_performance_intent = _contains_any(
            normalized,
            CURRENT_PERFORMANCE_TERMS,
        )
        has_performance_intent = (
            _contains_any(normalized, PERFORMANCE_TERMS)
            or has_current_performance_intent
        )
        has_comparative_metric_judgment = _has_metric_comparative_signal(normalized)
        has_judgment_intent = (
            _contains_any(normalized, JUDGMENT_TERMS)
            or has_comparative_metric_judgment
        )
        has_acceleration_intent = _contains_any(normalized, ACCELERATION_TERMS)
        if has_current_performance_intent:
            matched_rules.append("intent:current_performance")
        if has_comparative_metric_judgment:
            matched_rules.append("intent:metric_comparative_judgment")

        if _contains_any(normalized, MDA_TERMS) or (
            has_explanation_intent
            and ("margin" in normalized or "changed" in normalized or "变化" in normalized)
        ):
            matched_rules.append("section:mda")
            target_sections.append("Management's Discussion and Analysis")
            lexical_queries.append("management discussion analysis")

        if "section:risk_factors" in matched_rules:
            question_type = "risk"
            confidence = 0.9
        elif (
            has_liquidity_intent
            and not has_acceleration_intent
            and not has_judgment_intent
            and not _contains_any(normalized, TREND_TERMS)
        ):
            question_type = "liquidity"
            confidence = 0.82
        elif has_filing_summary_intent:
            question_type = "filing_summary"
            confidence = 0.84
        elif has_acceleration_intent:
            question_type = "growth_acceleration"
            confidence = 0.82
        elif has_explanation_intent:
            question_type = "drivers"
            confidence = 0.75
        elif has_judgment_intent:
            question_type = "metric_performance"
            confidence = 0.72
        elif has_performance_intent:
            question_type = "performance_overview"
            confidence = 0.68
        else:
            question_type = "prose"
            confidence = 0.45

        return IntentResult(
            question_type=question_type,
            target_sections=target_sections,
            lexical_queries=lexical_queries,
            matched_rules=matched_rules,
            confidence=confidence,
            has_explanation_intent=has_explanation_intent,
            has_performance_intent=has_performance_intent,
            has_judgment_intent=has_judgment_intent,
            has_acceleration_intent=has_acceleration_intent,
            has_liquidity_intent=has_liquidity_intent,
            has_filing_summary_intent=has_filing_summary_intent,
        )


class RuleMetricResolver:
    def resolve(self, query: NormalizedQuery, intent: IntentResult) -> MetricResolution:
        normalized = query.normalized
        matched_rules: list[str] = []
        metric_keys: list[str] = []
        unsupported_subject = _contains_any(normalized, BROAD_COMPANY_GROWTH_EXCLUDED_SUBJECTS)

        for metric_key, terms in METRIC_TERMS.items():
            if _contains_any(normalized, terms):
                matched_rules.append(f"metric:{metric_key}")
                metric_keys.append(metric_key)

        if (
            "revenue" not in metric_keys
            and _contains_any(normalized, SALES_ACTIVITY_TERMS)
            and _contains_any(normalized, SALES_ACTIVITY_CONTEXT_TERMS)
            and "selling expense" not in normalized
            and "selling expenses" not in normalized
        ):
            # 当 query 里出现 sell/sales/sold/销售，
            # 并且同时出现 growth/latest/performance/comparison 语境时，
            # 把它理解成 revenue。
            matched_rules.append("metric:revenue:sales_activity")
            metric_keys.append("revenue")

        if "net_income" not in metric_keys and _is_earnings_activity_question(normalized):
            matched_rules.append("metric:net_income:earnings_activity")
            metric_keys.append("net_income")

        broad_company_growth = False
        if not metric_keys and intent.has_liquidity_intent:
            matched_rules.append("metric:liquidity_default")
            metric_keys.extend(DEFAULT_LIQUIDITY_METRICS)

        if intent.question_type == "filing_summary" and (
            not metric_keys
            or (
                metric_keys == ["net_income"]
                and _contains_any(normalized, BROAD_EARNINGS_REPORT_TERMS)
            )
        ):
            matched_rules.append("metric:filing_summary_default")
            metric_keys = list(DEFAULT_SUMMARY_METRICS)

        if not metric_keys and (
            intent.has_judgment_intent or intent.has_acceleration_intent
        ):
            if _contains_any(normalized, PROFITABILITY_TERMS):
                matched_rules.append("metric:profitability_default")
                metric_keys.extend(DEFAULT_PROFITABILITY_METRICS)
            elif _contains_any(normalized, MARGIN_JUDGMENT_TERMS):
                matched_rules.append("metric:margin_default")
                metric_keys.extend(DEFAULT_MARGIN_METRICS)
            elif not unsupported_subject:
                matched_rules.append("metric:company_growth_default")
                metric_keys.extend(DEFAULT_COMPANY_GROWTH_METRICS)
                broad_company_growth = True

        metric_keys = _dedupe(metric_keys)
        confidence = self._confidence(metric_keys, matched_rules, unsupported_subject)
        return MetricResolution(
            metric_keys=metric_keys,
            matched_rules=matched_rules,
            confidence=confidence,
            broad_company_growth=broad_company_growth,
            unsupported_subject=unsupported_subject,
        )

    def apply_contextual_defaults(
        self,
        query: NormalizedQuery,
        intent: IntentResult,
        time: TimeResolution,
        resolution: MetricResolution,
    ) -> MetricResolution:
        if resolution.metric_keys:
            return resolution

        normalized = query.normalized
        matched_rules = list(resolution.matched_rules)
        metric_keys: list[str] = []
        broad_company_growth = False
        broad_company_change = False
        broad_company_performance = False

        if not resolution.unsupported_subject and _is_broad_performance_change_explanation(
            normalized,
            intent,
            time.time_scope,
        ):
            matched_rules.append("metric:company_change_default:performance_explanation")
            metric_keys.extend(DEFAULT_COMPANY_CHANGE_METRICS)
            broad_company_change = True
        elif not resolution.unsupported_subject and _is_broad_performance_driver_question(
            normalized,
            intent,
        ):
            matched_rules.append("metric:company_performance_default:driver_explanation")
            metric_keys.extend(DEFAULT_COMPANY_CHANGE_METRICS)
            broad_company_performance = True
        elif not resolution.unsupported_subject and _is_broad_company_growth_question(
            normalized,
            time.time_scope,
        ):
            matched_rules.append("metric:company_growth_default")
            metric_keys.extend(DEFAULT_COMPANY_GROWTH_METRICS)
            broad_company_growth = True
        elif not resolution.unsupported_subject and _is_vague_company_change_question(
            normalized,
            time.time_scope,
        ):
            matched_rules.append("metric:company_change_default")
            metric_keys.extend(DEFAULT_COMPANY_CHANGE_METRICS)
            broad_company_change = True
        elif not resolution.unsupported_subject and _is_vague_company_performance_question(
            normalized,
            has_performance_intent=intent.has_performance_intent,
        ):
            matched_rules.append("metric:company_performance_default")
            metric_keys.extend(DEFAULT_COMPANY_CHANGE_METRICS)
            broad_company_performance = True

        if not metric_keys:
            return resolution

        return MetricResolution(
            metric_keys=_dedupe(metric_keys),
            matched_rules=matched_rules,
            confidence=0.62,
            broad_company_growth=broad_company_growth,
            broad_company_change=broad_company_change,
            broad_company_performance=broad_company_performance,
            unsupported_subject=resolution.unsupported_subject,
        )

    def _confidence(
        self,
        metric_keys: list[str],
        matched_rules: list[str],
        unsupported_subject: bool,
    ) -> float:
        if unsupported_subject and not metric_keys:
            return 0.25
        if any(rule.startswith("metric:") and not rule.endswith("_default") for rule in matched_rules):
            return 0.95
        if metric_keys:
            return 0.7
        return 0.45


class EmbeddingMetricResolver:
    """Placeholder for future embedding-based metric routing."""

    def resolve(self, query: NormalizedQuery, intent: IntentResult) -> MetricResolution:
        return RuleMetricResolver().resolve(query, intent)


class TimeScopeResolver:
# 用户问的是 latest 吗？
# 用户问的是 trend / comparison 吗？
# 应该比较 quarter、YTD，还是 fiscal year？
# 应该查 10-Q、10-K，还是 8-K？
# 有没有时间冲突？
    def resolve(
        self,
        query: NormalizedQuery,
        intent: IntentResult,
        metric_keys: list[str],
    ) -> TimeResolution:
        normalized = query.normalized
        matched_rules: list[str] = []
        forms: list[str] = []
        ambiguities: list[str] = []
        comparison_basis = "none"
        comparison_candidates: list[str] = []
        default_comparison_basis: str | None = None

        time_scope = "unspecified"
        if _contains_any(normalized, LATEST_TERMS):
            matched_rules.append("time:latest")
            time_scope = "latest"
        if _contains_any(normalized, TREND_TERMS):
            matched_rules.append("time:comparison_trend")
            time_scope = "comparison_trend"
        if _contains_any(normalized, SALES_ACTIVITY_COMPARISON_TERMS):
            matched_rules.append("time:comparison_trend:sales_activity")
            time_scope = "comparison_trend"
        if metric_keys and _has_metric_comparative_signal(normalized, metric_keys):
            matched_rules.append("time:comparison_trend:metric_comparative")
            time_scope = "comparison_trend"
        if (
            metric_keys
            and intent.has_judgment_intent
            and _contains_any(normalized, PERFORMANCE_PERSISTENCE_TERMS)
        ):
            matched_rules.append("time:comparison_trend:persistent_judgment")
            time_scope = "comparison_trend"
        if intent.has_acceleration_intent and time_scope == "unspecified":
            matched_rules.append("time:comparison_trend")
            time_scope = "comparison_trend"
        if intent.has_liquidity_intent and time_scope == "unspecified":
            matched_rules.append("time:latest_default_liquidity")
            time_scope = "latest"
        if intent.question_type == "filing_summary" and time_scope == "unspecified":
            matched_rules.append("time:latest_default_filing_summary")
            time_scope = "latest"

        if "10-k" in normalized or "annual" in normalized:
            forms.append("10-K")
            matched_rules.append("form:10-K")
        if "10-q" in normalized or "quarter" in normalized or "quarterly" in normalized:
            forms.append("10-Q")
            matched_rules.append("form:10-Q")
        if "8-k" in normalized:
            forms.append("8-K")
            matched_rules.append("form:8-K")

        has_conflict = self._has_basis_conflict(normalized)

        if metric_keys and intent.has_acceleration_intent:
            (
                comparison_basis,
                comparison_candidates,
                default_comparison_basis,
                ambiguity,
            ) = _detect_growth_acceleration_basis(normalized)
            if ambiguity:
                ambiguities.append(ambiguity)
            matched_rules.append("comparison_basis:growth_acceleration")
            matched_rules.append(f"comparison_basis:{default_comparison_basis}:default")
        elif metric_keys and time_scope == "comparison_trend":
            comparison_basis = _detect_comparison_basis(normalized)
            if comparison_basis == "ambiguous":
                comparison_candidates = [
                    "latest_quarter_yoy",
                    "latest_ytd_yoy",
                    "latest_fy_yoy",
                ]
                default_comparison_basis = "latest_quarter_yoy"
                ambiguities.append(
                    "Question does not specify quarterly, year-to-date, annual, or multi-year growth."
                )
                matched_rules.append("comparison_basis:ambiguous")
            else:
                comparison_candidates = [comparison_basis]
                default_comparison_basis = comparison_basis
                matched_rules.append(f"comparison_basis:{comparison_basis}")

        if metric_keys and intent.has_judgment_intent and comparison_basis == "none":
            (
                comparison_basis,
                comparison_candidates,
                default_comparison_basis,
                ambiguity,
                rules,
            ) = self._default_metric_performance_basis(
                normalized,
                metric_keys,
                ambiguity_message=(
                    "Interpreted performance judgment as latest comparable period "
                    "because no period was specified."
                ),
                default_rule="comparison_basis:performance_judgment_default",
            )
            matched_rules.extend(rules)
            if ambiguity:
                ambiguities.append(ambiguity)
            if time_scope == "unspecified":
                time_scope = "latest"

        if metric_keys and intent.has_performance_intent and comparison_basis == "none":
            (
                comparison_basis,
                comparison_candidates,
                default_comparison_basis,
                ambiguity,
                rules,
            ) = self._default_metric_performance_basis(
                normalized,
                metric_keys,
                ambiguity_message=(
                    "Interpreted as latest comparable period performance because "
                    "no period was specified."
                ),
                default_rule="time:latest_default_metric_performance",
            )
            matched_rules.extend(rules)
            if ambiguity:
                ambiguities.append(ambiguity)
            if time_scope == "unspecified":
                time_scope = "latest"

        confidence = 0.88
        if comparison_basis == "ambiguous":
            confidence = 0.62
        elif time_scope == "unspecified":
            confidence = 0.55
        if has_conflict:
            confidence = min(confidence, 0.48)

        return TimeResolution(
            time_scope=time_scope,
            comparison_basis=comparison_basis,
            comparison_candidates=comparison_candidates,
            default_comparison_basis=default_comparison_basis,
            ambiguities=ambiguities,
            forms=_dedupe(forms),
            matched_rules=matched_rules,
            confidence=confidence,
            has_conflict=has_conflict,
        )

    def _default_metric_performance_basis(
        self,
        normalized: str,
        metric_keys: list[str],
        *,
        ambiguity_message: str,
        default_rule: str,
    ) -> tuple[str, list[str], str, str | None, list[str]]:
        detected_basis = _detect_comparison_basis(normalized)
        if detected_basis == "ambiguous":
            default_basis = _default_comparison_basis_for_metrics(metric_keys)
            return (
                default_basis,
                [default_basis],
                default_basis,
                ambiguity_message,
                [default_rule, f"comparison_basis:{default_basis}:default"],
            )
        return (
            detected_basis,
            [detected_basis],
            detected_basis,
            None,
            [f"comparison_basis:{detected_basis}"],
        )

    def _has_basis_conflict(self, normalized: str) -> bool:
        hits = [
            _contains_any(normalized, QUARTERLY_BASIS_TERMS),
            _contains_any(normalized, YTD_BASIS_TERMS),
            _contains_any(normalized, FY_BASIS_TERMS),
        ]
        return sum(1 for hit in hits if hit) > 1


class RetrievalStrategyBuilder:
    def build(
        self,
        query: NormalizedQuery,
        intent: IntentResult,
        metrics: MetricResolution,
        time: TimeResolution,
    ) -> RetrievalStrategy:
        target_sections = list(intent.target_sections)
        forms = list(time.forms)
        matched_rules: list[str] = []

        if query.form_type and query.form_type not in forms:
            forms.append(query.form_type)
        if query.section and query.section not in target_sections:
            target_sections.append(query.section)

        if intent.has_liquidity_intent and metrics.metric_keys and not intent.has_explanation_intent:
            liquidity_sections = (
                ["Liquidity", "Cash Flows", "Management's Discussion and Analysis"]
                if intent.question_type == "liquidity"
                else ["Cash Flows", "Management's Discussion and Analysis"]
            )
            for section in liquidity_sections:
                if section not in target_sections:
                    target_sections.append(section)
            matched_rules.append("section:liquidity")

        if intent.question_type == "filing_summary" and metrics.metric_keys:
            summary_sections = [
                "Financial Statements",
                "Management's Discussion and Analysis",
                "Liquidity",
                "Cash Flows",
            ]
            if "10-K" in forms:
                summary_sections.append("Risk Factors")
            for section in summary_sections:
                if section not in target_sections:
                    target_sections.append(section)
            matched_rules.append("section:filing_summary")

        if (
            intent.has_explanation_intent
            and not metrics.metric_keys
            and not target_sections
            and not _contains_any(query.normalized, NON_FILING_EXPLANATION_SUBJECT_TERMS)
        ):
            target_sections.append("Management's Discussion and Analysis")
            matched_rules.append("section:mda_driver_context")

        preferred_form = _preferred_form_for_comparison_basis(time.default_comparison_basis)
        if (
            intent.question_type == "liquidity"
            and time.time_scope == "latest"
            and not query.form_type
            and not forms
        ):
            forms.append("10-Q")
            matched_rules.append("form:10-Q:liquidity_latest_default")
        if (
            intent.question_type == "filing_summary"
            and time.time_scope == "latest"
            and not query.form_type
            and not forms
        ):
            forms.append("10-Q")
            matched_rules.append("form:10-Q:filing_summary_latest_default")
        if metrics.broad_company_change and preferred_form and preferred_form not in forms:
            forms.append(preferred_form)
            matched_rules.append(f"form:{preferred_form}:company_change_default")
        if (
            intent.has_acceleration_intent
            and preferred_form
            and preferred_form not in forms
        ):
            forms.append(preferred_form)
            matched_rules.append(f"form:{preferred_form}:growth_acceleration_default")

        if metrics.metric_keys and time.comparison_basis != "none" and not target_sections:
            matched_rules.append("section:financial_statements_metric_trend")
            target_sections.append("Financial Statements")

        if (
            metrics.metric_keys
            and time.comparison_basis != "none"
            and intent.has_acceleration_intent
            and "Management's Discussion and Analysis" not in target_sections
        ):
            matched_rules.append("section:mda_growth_acceleration")
            target_sections.append("Management's Discussion and Analysis")
        elif (
            metrics.metric_keys
            and time.comparison_basis != "none"
            and (metrics.broad_company_growth or metrics.broad_company_change)
            and "Management's Discussion and Analysis" not in target_sections
        ):
            matched_rules.append(
                "section:mda_company_change"
                if metrics.broad_company_change
                else "section:mda_company_growth"
            )
            target_sections.append("Management's Discussion and Analysis")
        elif (
            metrics.metric_keys
            and time.comparison_basis != "none"
            and metrics.broad_company_performance
            and "Management's Discussion and Analysis" not in target_sections
        ):
            matched_rules.append("section:mda_company_performance")
            target_sections.append("Management's Discussion and Analysis")
        elif (
            metrics.metric_keys
            and time.comparison_basis != "none"
            and intent.has_judgment_intent
            and "Management's Discussion and Analysis" not in target_sections
        ):
            matched_rules.append("section:mda_performance_judgment")
            target_sections.append("Management's Discussion and Analysis")
        elif (
            metrics.metric_keys
            and time.comparison_basis != "none"
            and intent.has_performance_intent
            and time.time_scope != "comparison_trend"
            and "Management's Discussion and Analysis" not in target_sections
        ):
            matched_rules.append("section:mda_metric_performance")
            target_sections.append("Management's Discussion and Analysis")

        if (
            metrics.metric_keys
            and intent.has_explanation_intent
            and "Management's Discussion and Analysis" not in target_sections
        ):
            matched_rules.append("section:mda_metric_explanation")
            target_sections.append("Management's Discussion and Analysis")

        question_type = self._legacy_question_type(
            intent,
            metrics,
            time,
            target_sections,
            matched_rules,
        )
        needs_financial_facts = bool(metrics.metric_keys) and question_type not in {"risk", "prose"}
        needs_metric_comparisons = needs_financial_facts and _comparison_requested(
            time.comparison_basis,
            time.comparison_candidates,
        )
        evidence_roles = self._evidence_roles(question_type, target_sections, needs_metric_comparisons)

        confidence = 0.88
        if metrics.broad_company_growth or metrics.broad_company_change or metrics.broad_company_performance:
            confidence = 0.74
        if not metrics.metric_keys and question_type in {"comparison", "prose"}:
            confidence = 0.55

        return RetrievalStrategy(
            question_type=question_type,
            target_sections=_dedupe(target_sections),
            forms=_dedupe(forms),
            matched_rules=matched_rules,
            confidence=confidence,
            needs_financial_facts=needs_financial_facts,
            needs_text_chunks=True,
            needs_metric_comparisons=needs_metric_comparisons,
            evidence_roles=evidence_roles,
        )

    def _legacy_question_type(
        self,
        intent: IntentResult,
        metrics: MetricResolution,
        time: TimeResolution,
        target_sections: list[str],
        strategy_rules: list[str],
    ) -> str:
        if metrics.metric_keys and intent.has_acceleration_intent and time.comparison_basis != "none":
            return "growth_acceleration"
        if metrics.metric_keys and intent.has_explanation_intent:
            return "mixed"
        if metrics.metric_keys and any(
            rule in {"section:mda", "section:mda_metric_explanation"}
            for rule in [*intent.matched_rules, *strategy_rules]
        ):
            return "mixed"
        if metrics.metric_keys and intent.question_type == "liquidity":
            return "liquidity"
        if metrics.metric_keys and intent.question_type == "filing_summary":
            return "filing_summary"
        if (
            metrics.metric_keys
            and intent.has_liquidity_intent
            and time.comparison_basis != "none"
        ):
            return "trend"
        if metrics.metric_keys and metrics.broad_company_change and time.comparison_basis != "none":
            return "broad_comparison"
        if metrics.metric_keys and metrics.broad_company_performance and time.comparison_basis != "none":
            return "performance_overview"
        if metrics.metric_keys and intent.has_judgment_intent and time.comparison_basis != "none":
            return "performance_judgment"
        if (
            metrics.metric_keys
            and time.comparison_basis != "none"
            and set(target_sections).issubset(
                {"Financial Statements", "Management's Discussion and Analysis"}
            )
        ):
            return "trend"
        if metrics.metric_keys and target_sections:
            return "mixed"
        if metrics.metric_keys and time.time_scope == "comparison_trend":
            return "trend"
        if metrics.metric_keys:
            return "metric"
        if any(rule == "section:risk_factors" for rule in intent.matched_rules):
            return "risk"
        if any(rule == "section:mda" for rule in intent.matched_rules):
            return "management_discussion"
        if time.time_scope == "comparison_trend":
            return "comparison"
        return "prose"

    def _evidence_roles(
        self,
        question_type: str,
        target_sections: list[str],
        needs_metric_comparisons: bool,
    ) -> list[str]:
        return _evidence_roles_for(
            question_type,
            target_sections,
            needs_metric_comparisons,
        )


class QueryExpansionBuilder:
    def build(
        self,
        query: NormalizedQuery,
        intent: IntentResult,
        metrics: MetricResolution,
        strategy: RetrievalStrategy,
        time: TimeResolution | None = None,
    ) -> ExpansionResult:
        lexical_queries: list[str] = [*intent.lexical_queries]
        lexical_queries.extend(_build_metric_lexical_queries(metrics.metric_keys))

        if metrics.broad_company_growth:
            lexical_queries.extend(COMPANY_GROWTH_LEXICAL_QUERIES)
        if metrics.broad_company_change:
            lexical_queries.extend(COMPANY_CHANGE_LEXICAL_QUERIES)
        if metrics.broad_company_performance:
            lexical_queries.extend(COMPANY_QUARTER_PERFORMANCE_LEXICAL_QUERIES)
        if intent.has_acceleration_intent and metrics.metric_keys:
            lexical_queries.extend(GROWTH_ACCELERATION_LEXICAL_QUERIES)
        if intent.has_judgment_intent and metrics.metric_keys:
            lexical_queries.extend(PERFORMANCE_JUDGMENT_LEXICAL_QUERIES)
        if intent.has_liquidity_intent:
            lexical_queries.extend(LIQUIDITY_LEXICAL_QUERIES)
        if intent.has_filing_summary_intent:
            lexical_queries.extend(FILING_SUMMARY_LEXICAL_QUERIES)
        if (
            intent.has_explanation_intent
            and "Management's Discussion and Analysis" in strategy.target_sections
        ):
            lexical_queries.append("management discussion analysis")

        lexical_queries = _dedupe([query for query in lexical_queries if query.strip()])
        if not lexical_queries:
            lexical_queries = [query.original]

        dense_query_specs = _build_dense_query_specs(
            query,
            intent,
            metrics,
            strategy,
            time=time,
        )
        dense_queries = [spec["text"] for spec in dense_query_specs]

        return ExpansionResult(
            dense_queries=dense_queries,
            dense_query_specs=dense_query_specs,
            lexical_queries=lexical_queries,
        )


def _build_dense_query_specs(
    query: NormalizedQuery,
    intent: IntentResult,
    metrics: MetricResolution,
    strategy: RetrievalStrategy,
    *,
    time: TimeResolution | None,
) -> list[dict[str, Any]]:
    slot_query = _build_slot_dense_query(intent, metrics, strategy, time=time)
    role_queries = _build_role_dense_queries(metrics, strategy, time=time)
    specs = [
        {"role": "slot", "text": slot_query, "weight": 1.0},
        *role_queries,
        {"role": "original", "text": query.original, "weight": 0.4},
    ]
    deduped: list[dict[str, Any]] = []
    seen_text: set[str] = set()
    for spec in specs:
        text_value = " ".join(str(spec.get("text", "")).split())
        if not text_value or text_value in seen_text:
            continue
        seen_text.add(text_value)
        deduped.append(
            {
                "role": str(spec.get("role", "dense")),
                "text": text_value,
                "weight": float(spec.get("weight", 1.0)),
            }
        )
    return deduped


def _build_slot_dense_query(
    intent: IntentResult,
    metrics: MetricResolution,
    strategy: RetrievalStrategy,
    *,
    time: TimeResolution | None,
) -> str:
    comparison_basis = _dense_comparison_basis(time)
    terms = [
        _dense_time_phrase(time),
        DENSE_INTENT_PHRASES.get(strategy.question_type, ""),
        *_dense_metric_phrases(metrics.metric_keys),
        DENSE_COMPARISON_PHRASES.get(comparison_basis, ""),
        *_dense_section_phrases(strategy.target_sections),
    ]
    if intent.has_explanation_intent:
        terms.append("drivers reasons primarily due to higher lower")
    return _join_dense_terms(terms)


def _build_role_dense_queries(
    metrics: MetricResolution,
    strategy: RetrievalStrategy,
    *,
    time: TimeResolution | None,
) -> list[dict[str, Any]]:
    metric_phrases = _dense_metric_phrases(metrics.metric_keys)
    statement_metric_phrases = _dense_metric_phrases(
        metrics.metric_keys,
        include_statement_terms=True,
    )
    comparison_phrase = DENSE_COMPARISON_PHRASES.get(_dense_comparison_basis(time), "")
    time_phrase = _dense_time_phrase(time)
    queries: list[dict[str, Any]] = []

    if "primary_financial_statement_chunks" in strategy.evidence_roles:
        statement_context = (
            "financial statements statements of cash flows operating activities"
            if "Cash Flows" in strategy.target_sections
            else "financial statements statements of operations"
        )
        queries.append(
            {
                "role": "financial_statement",
                "text": _join_dense_terms(
                    [
                        time_phrase,
                        statement_context,
                        *statement_metric_phrases,
                        comparison_phrase,
                    ]
                ),
                "weight": 0.9,
            }
        )
    if "mda_explanation_chunks" in strategy.evidence_roles:
        queries.append(
            {
                "role": "mda_drivers",
                "text": _join_dense_terms(
                    [
                        "management discussion analysis results of operations",
                        "drivers reasons primarily due to higher lower",
                        *metric_phrases,
                        comparison_phrase,
                    ]
                ),
                "weight": 0.85,
            }
        )
    if "segment_or_product_breakdown_chunks" in strategy.evidence_roles:
        queries.append(
            {
                "role": "segment_breakdown",
                "text": _join_dense_terms(
                    [
                        "products and services performance segment operating performance",
                        "product category geographic segment revenue growth",
                        *metric_phrases,
                    ]
                ),
                "weight": 0.75,
            }
        )
    if "risk_factor_chunks" in strategy.evidence_roles:
        queries.append(
            {
                "role": "risk_factors",
                "text": _join_dense_terms(
                    [
                        time_phrase,
                        "risk factors item 1a",
                        "business operational financial market regulatory risks",
                    ]
                ),
                "weight": 1.0,
            }
        )
    return queries


def _dense_time_phrase(time: TimeResolution | None) -> str:
    if time is None:
        return ""
    comparison_basis = _dense_comparison_basis(time)
    if comparison_basis in DENSE_TIME_PHRASES:
        return DENSE_TIME_PHRASES[comparison_basis]
    if time.time_scope == "latest":
        return "latest most recent filing period"
    if time.time_scope == "comparison_trend":
        return "year-over-year trend compared to prior period"
    return ""


def _dense_comparison_basis(time: TimeResolution | None) -> str:
    if time is None:
        return "none"
    if time.comparison_basis not in {"none", "ambiguous"}:
        return time.comparison_basis
    if time.comparison_basis == "ambiguous":
        return "ambiguous"
    if time.default_comparison_basis:
        return time.default_comparison_basis
    return "none"


def _dense_metric_phrases(
    metric_keys: list[str],
    *,
    include_statement_terms: bool = False,
) -> list[str]:
    phrases: list[str] = []
    for metric_key in metric_keys:
        profile = get_metric_profile(metric_key)
        label = METRIC_LABELS.get(metric_key, metric_key).lower()
        phrases.append(label)
        if profile is None:
            continue
        phrases.extend(profile.strong_terms[:2])
        if include_statement_terms:
            phrases.extend(profile.statement_terms[:1])
    return _dedupe(phrases)


def _dense_section_phrases(target_sections: list[str]) -> list[str]:
    phrases: list[str] = []
    for section in target_sections:
        if section in DENSE_SECTION_PHRASES:
            phrases.append(DENSE_SECTION_PHRASES[section])
    return phrases


def _join_dense_terms(terms: list[str]) -> str:
    words_used = 0
    selected_terms: list[str] = []
    for term in _dedupe([" ".join(term.split()) for term in terms if term.strip()]):
        term_word_count = len(term.split())
        if words_used and words_used + term_word_count > MAX_DENSE_QUERY_WORDS:
            continue
        selected_terms.append(term)
        words_used += term_word_count
    return " ".join(selected_terms)


def _comparison_requested(
    comparison_basis: str,
    comparison_candidates: list[str],
) -> bool:
    return bool(
        comparison_candidates
        or comparison_basis not in {"none", "ambiguous"}
    )


def _evidence_roles_for(
    question_type: str,
    target_sections: list[str],
    needs_metric_comparisons: bool,
) -> list[str]:
    roles: list[str] = []
    if needs_metric_comparisons:
        roles.append("metric_comparisons")
    if "Financial Statements" in target_sections or "Cash Flows" in target_sections:
        roles.append("primary_financial_statement_chunks")
    if "Management's Discussion and Analysis" in target_sections:
        roles.append("mda_explanation_chunks")
    if question_type in {"broad_comparison", "performance_overview"}:
        roles.append("segment_or_product_breakdown_chunks")
    if "Risk Factors" in target_sections or question_type == "risk":
        roles.append("risk_factor_chunks")
    return _dedupe(roles)


class PlanValidator:
    def validate(
        self,
        plan: RetrievalPlan,
        query: NormalizedQuery,
        *,
        planner_source: str,
        validation_confidence: float = 1.0,
        has_conflict: bool = False,
    ) -> RetrievalPlan:
        question_type = (
            plan.question_type
            if plan.question_type in VALID_QUESTION_TYPES
            else "prose"
        )
        time_scope = (
            plan.time_scope if plan.time_scope in VALID_TIME_SCOPES else "unspecified"
        )
        metric_keys = [
            metric_key
            for metric_key in _dedupe(plan.metric_keys)
            if metric_key in METRIC_RETRIEVAL_PROFILES
        ]
        forms = [form for form in _dedupe(form.upper() for form in plan.forms) if form in VALID_FORMS]
        matched_rules = list(plan.matched_rules)

        if query.form_type:
            forms = [query.form_type] if query.form_type in VALID_FORMS else forms

        target_sections = [
            section
            for section in _dedupe(plan.target_sections)
            if section in VALID_TARGET_SECTIONS
        ]
        if query.section and query.section not in target_sections:
            target_sections.append(query.section)

        comparison_basis = (
            plan.comparison_basis
            if plan.comparison_basis in VALID_COMPARISON_BASES
            else "none"
        )
        comparison_candidates = [
            candidate
            for candidate in _dedupe(plan.comparison_candidates)
            if candidate in VALID_COMPARISON_BASES and candidate not in {"none", "ambiguous"}
        ]
        default_comparison_basis = (
            plan.default_comparison_basis
            if plan.default_comparison_basis in VALID_COMPARISON_BASES
            else None
        )

        preferred_forms = [
            form
            for form in _dedupe(form.upper() for form in plan.preferred_forms)
            if form in VALID_FORMS
        ]
        preferred_form = None
        if comparison_basis != "ambiguous":
            preferred_form = _preferred_form_for_comparison_basis(
                default_comparison_basis or comparison_basis
            )
        elif default_comparison_basis:
            preferred_form = _preferred_form_for_comparison_basis(default_comparison_basis)
        if preferred_form and not query.form_type and preferred_form not in preferred_forms:
            preferred_forms.append(preferred_form)
        if (
            question_type in {"liquidity", "filing_summary"}
            and forms
            and not query.form_type
            and forms[0] not in preferred_forms
        ):
            preferred_forms.append(forms[0])
        if (
            question_type in {"liquidity", "filing_summary"}
            and time_scope == "latest"
            and not query.form_type
            and not forms
            and "10-Q" not in preferred_forms
        ):
            preferred_forms.append("10-Q")
        if (
            preferred_form
            and comparison_basis != "ambiguous"
            and not query.form_type
            and preferred_form not in forms
        ):
            forms.append(preferred_form)
        if query.form_type and query.form_type in VALID_FORMS:
            preferred_forms = [query.form_type]

        needs_financial_facts = bool(metric_keys) and question_type not in {"risk", "prose"}
        needs_metric_comparisons = needs_financial_facts and _comparison_requested(
            comparison_basis,
            comparison_candidates,
        )
        evidence_roles = _evidence_roles_for(
            question_type,
            target_sections,
            needs_metric_comparisons,
        )

        confidence_breakdown = dict(plan.confidence_breakdown)
        validation_value = max(0.0, validation_confidence - (0.25 if has_conflict else 0.0))
        confidence_breakdown["validation_confidence"] = round(validation_value, 2)
        metric_optional = question_type in {"risk", "management_discussion"}
        confidence_breakdown["overall_confidence"] = round(
            _overall_confidence(
                confidence_breakdown,
                has_metric=bool(metric_keys),
                metric_optional=metric_optional,
            ),
            2,
        )
        if has_conflict:
            matched_rules.append("validation:time_basis_conflict")

        return RetrievalPlan(
            question_type=question_type,
            target_sections=target_sections,
            metric_keys=metric_keys,
            time_scope=time_scope,
            comparison_basis=comparison_basis,
            comparison_candidates=comparison_candidates,
            default_comparison_basis=default_comparison_basis,
            ambiguities=plan.ambiguities,
            forms=_dedupe(forms),
            preferred_forms=_dedupe(preferred_forms),
            dense_queries=plan.dense_queries,
            dense_query_specs=plan.dense_query_specs,
            lexical_queries=plan.lexical_queries,
            rule_confidence=confidence_breakdown["overall_confidence"],
            matched_rules=_dedupe(matched_rules),
            planner_source=planner_source,
            confidence_breakdown=confidence_breakdown,
            needs_financial_facts=needs_financial_facts,
            needs_text_chunks=plan.needs_text_chunks,
            needs_metric_comparisons=needs_metric_comparisons,
            evidence_roles=evidence_roles,
            requires_llm_fallback_reason=plan.requires_llm_fallback_reason,
        )


class LLMQueryPlanner:
    allowed_fields = {
        "question_type",
        "metric_keys",
        "time_scope",
        "comparison_basis",
        "comparison_candidates",
        "target_sections",
        "forms",
        "reasoning_summary",
        "confidence",
    }

    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()

    def plan_candidate(self, question: str) -> dict[str, Any]:
        api_key = self._settings.openai_api_key
        if api_key is None or not api_key.get_secret_value().strip():
            raise RuntimeError("OPENAI_API_KEY must be configured for LLM query planning.")

        try:
            from openai import OpenAI
        except ImportError as exc:
            raise RuntimeError("The openai package must be installed for LLM query planning.") from exc

        client = OpenAI(
            api_key=api_key.get_secret_value(),
            timeout=self._settings.query_planner_llm_timeout_seconds,
        )
        response = client.chat.completions.create(
            model=self._settings.query_planner_llm_model,
            temperature=0,
            response_format={"type": "json_object"},
            messages=[
                {
                    "role": "system",
                    "content": _llm_planner_system_prompt(),
                },
                {
                    "role": "user",
                    "content": f"Classify this user question into planner slots:\n{question}",
                },
            ],
        )
        content = response.choices[0].message.content or "{}"
        candidate = _parse_llm_json(content)
        if not isinstance(candidate, dict):
            raise ValueError("LLM planner response must be a JSON object.")
        unknown_fields = set(candidate) - self.allowed_fields
        if unknown_fields:
            raise ValueError(f"LLM planner returned unsupported fields: {sorted(unknown_fields)}")
        return candidate


def _llm_planner_system_prompt() -> str:
    return f"""
You are a query planning classifier for an equity research retrieval system.

Convert the user question into one strict JSON object. Do not answer the question.
Do not retrieve facts. Do not invent metrics, forms, sections, comparison bases, or fields.
Use only the allowed values below.

Allowed JSON fields:
- question_type
- metric_keys
- time_scope
- comparison_basis
- comparison_candidates
- target_sections
- forms
- reasoning_summary
- confidence

Allowed question_type values:
{_format_allowed_values(VALID_QUESTION_TYPES)}

Allowed metric_keys values:
{_format_allowed_values(METRIC_RETRIEVAL_PROFILES.keys())}

Allowed time_scope values:
{_format_allowed_values(VALID_TIME_SCOPES)}

Allowed comparison_basis values:
{_format_allowed_values(VALID_COMPARISON_BASES)}

Allowed comparison_candidates values:
{_format_allowed_values(candidate for candidate in VALID_COMPARISON_BASES if candidate not in {"none", "ambiguous"})}

Allowed target_sections values:
{_format_allowed_values(VALID_TARGET_SECTIONS)}

Allowed forms values:
{_format_allowed_values(VALID_FORMS)}

Rules:
- Return only valid JSON.
- confidence must be a number between 0 and 1.
- Use metric_keys=[] for risk, prose, stock price, valuation, market news, peer comparison, or non-filing context.
- Interpret now, currently, recently, and lately as the latest available SEC filing period, not live market data.
- For broad current company performance, use question_type="performance_overview", metric_keys=["revenue","gross_margin","operating_income","net_income"], time_scope="latest", comparison_basis="latest_quarter_yoy", comparison_candidates=["latest_quarter_yoy"], target_sections=["Financial Statements","Management's Discussion and Analysis"], forms=["10-Q"].
- For broad company growth without a specific period, use question_type="trend", metric_keys=["revenue","operating_income","net_income"], time_scope="comparison_trend", comparison_basis="ambiguous", comparison_candidates=["latest_quarter_yoy","latest_ytd_yoy","latest_fy_yoy"], target_sections=["Financial Statements","Management's Discussion and Analysis"], forms=[].
- For liquidity, cash position, enough cash, capital resources, or generic cash flow questions, use metric_keys=["operating_cash_flow","free_cash_flow"] unless a more specific allowed cash-flow metric is explicitly named.
- For current liquidity state, use question_type="liquidity", time_scope="latest", target_sections=["Liquidity","Cash Flows","Management's Discussion and Analysis"], forms=["10-Q"]. Use comparison_basis="none" for cash sufficiency questions, and comparison_basis="latest_quarter_yoy" when the user asks how liquidity or cash flow is performing.
- For filing, 10-Q, 10-K, earnings report, or recent financial-results summaries, use question_type="filing_summary", metric_keys=["revenue","gross_margin","operating_income","net_income","operating_cash_flow","free_cash_flow"], time_scope="latest", comparison_basis="none", comparison_candidates=[], target_sections=["Financial Statements","Management's Discussion and Analysis","Liquidity","Cash Flows"], forms=["10-Q"] unless the user explicitly asks for 10-K or annual report.
- If the user asks to summarize risks, use question_type="risk"; if the user asks to summarize liquidity, use question_type="liquidity".
- For stock price, valuation, market news, or non-filing questions, use question_type="prose", metric_keys=[], comparison_basis="none", comparison_candidates=[], target_sections=[], forms=[].

Examples:
User: How is Apple doing now?
JSON: {{"question_type":"performance_overview","metric_keys":["revenue","gross_margin","operating_income","net_income"],"time_scope":"latest","comparison_basis":"latest_quarter_yoy","comparison_candidates":["latest_quarter_yoy"],"target_sections":["Financial Statements","Management's Discussion and Analysis"],"forms":["10-Q"],"reasoning_summary":"Broad current performance question mapped to latest quarterly financial performance.","confidence":0.86}}

User: Is Apple growing?
JSON: {{"question_type":"trend","metric_keys":["revenue","operating_income","net_income"],"time_scope":"comparison_trend","comparison_basis":"ambiguous","comparison_candidates":["latest_quarter_yoy","latest_ytd_yoy","latest_fy_yoy"],"target_sections":["Financial Statements","Management's Discussion and Analysis"],"forms":[],"reasoning_summary":"Broad growth question without a specified period; keep comparison basis ambiguous.","confidence":0.78}}

User: Does Apple have enough cash?
JSON: {{"question_type":"liquidity","metric_keys":["operating_cash_flow","free_cash_flow"],"time_scope":"latest","comparison_basis":"none","comparison_candidates":[],"target_sections":["Liquidity","Cash Flows","Management's Discussion and Analysis"],"forms":["10-Q"],"reasoning_summary":"Cash sufficiency question mapped to current liquidity and cash-flow evidence without forcing a period comparison.","confidence":0.82}}

User: Summarize Apple's latest earnings report.
JSON: {{"question_type":"filing_summary","metric_keys":["revenue","gross_margin","operating_income","net_income","operating_cash_flow","free_cash_flow"],"time_scope":"latest","comparison_basis":"none","comparison_candidates":[],"target_sections":["Financial Statements","Management's Discussion and Analysis","Liquidity","Cash Flows"],"forms":["10-Q"],"reasoning_summary":"Latest earnings report summary mapped to broad filing sections and core financial metrics.","confidence":0.86}}

User: What is Apple's stock price doing now?
JSON: {{"question_type":"prose","metric_keys":[],"time_scope":"latest","comparison_basis":"none","comparison_candidates":[],"target_sections":[],"forms":[],"reasoning_summary":"Stock price is outside SEC filing financial metric retrieval.","confidence":0.7}}
""".strip()


def _format_allowed_values(values: Any) -> str:
    return "\n".join(f"- {value}" for value in sorted(values))


def _parse_llm_json(content: str) -> dict[str, Any]:
    stripped = content.strip()
    if stripped.startswith("```"):
        stripped = re.sub(r"^```(?:json)?\s*", "", stripped)
        stripped = re.sub(r"\s*```$", "", stripped)
    parsed = json.loads(stripped)
    if not isinstance(parsed, dict):
        raise ValueError("LLM planner response must be a JSON object.")
    return parsed


class QueryPlanner:
    def __init__(
        self,
        *,
        settings: Settings | None = None,
        normalizer: QueryNormalizer | None = None,
        intent_parser: IntentParser | None = None,
        metric_resolver: RuleMetricResolver | None = None,
        time_resolver: TimeScopeResolver | None = None,
        strategy_builder: RetrievalStrategyBuilder | None = None,
        expansion_builder: QueryExpansionBuilder | None = None,
        validator: PlanValidator | None = None,
        llm_planner: LLMPlanner | None = None,
    ) -> None:
        self._settings = settings or get_settings()
        self._normalizer = normalizer or QueryNormalizer()
        self._intent_parser = intent_parser or IntentParser()
        self._metric_resolver = metric_resolver or RuleMetricResolver()
        self._time_resolver = time_resolver or TimeScopeResolver()
        self._strategy_builder = strategy_builder or RetrievalStrategyBuilder()
        self._expansion_builder = expansion_builder or QueryExpansionBuilder()
        self._validator = validator or PlanValidator()
        self._llm_planner = llm_planner

    def plan(
        self,
        question: str,
        *,
        form_type: str | None = None,
        section: str | None = None,
    ) -> RetrievalPlan:
        query = self._normalizer.normalize(question, form_type=form_type, section=section)
        rule_plan = self._build_rule_plan(query)

        threshold = self._settings.query_planner_llm_confidence_threshold
        fallback_reason = _llm_fallback_reason(rule_plan, threshold=threshold)
        if fallback_reason:
            rule_plan = replace(
                rule_plan,
                requires_llm_fallback_reason=fallback_reason,
            )
        should_fallback = (
            self._settings.query_planner_mode == "rule_with_llm_fallback"
            and fallback_reason is not None
        )
        if not should_fallback:
            return rule_plan

        try:
            llm_candidate = self._get_llm_planner().plan_candidate(question)
            return self._build_llm_plan(
                query,
                llm_candidate,
                fallback_plan=rule_plan,
                fallback_reason=fallback_reason,
            )
        except Exception:
            return RetrievalPlan(
                **{
                    **rule_plan.to_dict(),
                    "matched_rules": _dedupe([*rule_plan.matched_rules, "llm_fallback:failed"]),
                }
            )

    def _build_rule_plan(self, query: NormalizedQuery) -> RetrievalPlan:
        intent = self._intent_parser.parse(query)
        metrics = self._metric_resolver.resolve(query, intent)
        time = self._time_resolver.resolve(query, intent, metrics.metric_keys)
        metrics = self._metric_resolver.apply_contextual_defaults(query, intent, time, metrics)
        if metrics.metric_keys and not time.comparison_candidates and time.comparison_basis == "none":
            time = self._time_resolver.resolve(query, intent, metrics.metric_keys)
        strategy = self._strategy_builder.build(query, intent, metrics, time)
        expansion = self._expansion_builder.build(query, intent, metrics, strategy, time=time)

        confidence_breakdown = {
            "intent_confidence": round(intent.confidence, 2),
            "metric_confidence": round(metrics.confidence, 2),
            "time_confidence": round(time.confidence, 2),
            "strategy_confidence": round(strategy.confidence, 2),
        }
        plan = RetrievalPlan(
            question_type=strategy.question_type,
            target_sections=strategy.target_sections,
            metric_keys=metrics.metric_keys,
            time_scope=time.time_scope,
            comparison_basis=time.comparison_basis,
            comparison_candidates=time.comparison_candidates,
            default_comparison_basis=time.default_comparison_basis,
            ambiguities=time.ambiguities,
            forms=strategy.forms,
            dense_queries=expansion.dense_queries,
            dense_query_specs=expansion.dense_query_specs,
            lexical_queries=expansion.lexical_queries,
            rule_confidence=0,
            matched_rules=[
                *intent.matched_rules,
                *metrics.matched_rules,
                *time.matched_rules,
                *strategy.matched_rules,
            ],
            planner_source="rule",
            confidence_breakdown=confidence_breakdown,
            needs_financial_facts=strategy.needs_financial_facts,
            needs_text_chunks=strategy.needs_text_chunks,
            needs_metric_comparisons=strategy.needs_metric_comparisons,
            evidence_roles=strategy.evidence_roles,
        )
        return self._validator.validate(
            plan,
            query,
            planner_source="rule_validated",
            has_conflict=time.has_conflict,
        )

    def _build_llm_plan(
        self,
        query: NormalizedQuery,
        candidate: dict[str, Any],
        *,
        fallback_plan: RetrievalPlan,
        fallback_reason: str,
    ) -> RetrievalPlan:
        unknown_fields = set(candidate) - LLMQueryPlanner.allowed_fields
        if unknown_fields:
            raise ValueError(f"LLM planner returned unsupported fields: {sorted(unknown_fields)}")

        metric_keys = _as_str_list(candidate.get("metric_keys"))
        question_type = str(candidate.get("question_type") or fallback_plan.question_type)
        comparison_basis = str(candidate.get("comparison_basis") or "none")
        comparison_candidates = _as_str_list(candidate.get("comparison_candidates"))
        target_sections = _as_str_list(candidate.get("target_sections"))
        forms = _as_str_list(candidate.get("forms"))
        confidence = _coerce_confidence(candidate.get("confidence"), fallback=0.6)

        default_comparison_basis = (
            comparison_candidates[0]
            if comparison_candidates
            else comparison_basis
            if comparison_basis not in {"none", "ambiguous"}
            else None
        )
        llm_time = TimeResolution(
            time_scope=str(candidate.get("time_scope") or "unspecified"),
            comparison_basis=comparison_basis,
            comparison_candidates=comparison_candidates,
            default_comparison_basis=default_comparison_basis,
            ambiguities=[],
            forms=forms,
            matched_rules=["time:llm_candidate"],
            confidence=confidence,
        )
        intent = IntentResult(
            question_type=question_type,
            target_sections=target_sections,
            lexical_queries=[],
            matched_rules=["planner:llm_fallback"],
            confidence=confidence,
            has_explanation_intent=question_type in {"drivers", "mixed"},
            has_performance_intent=question_type in {
                "metric_performance",
                "performance_overview",
                "trend",
            },
            has_judgment_intent=question_type in {"metric_performance", "performance_judgment"},
            has_acceleration_intent=question_type == "growth_acceleration",
            has_liquidity_intent=question_type == "liquidity",
            has_filing_summary_intent=question_type == "filing_summary",
        )
        metrics = MetricResolution(
            metric_keys=metric_keys,
            matched_rules=[f"metric:{metric_key}:llm" for metric_key in metric_keys],
            confidence=confidence,
        )
        needs_financial_facts = bool(metric_keys) and question_type not in {"risk", "prose"}
        needs_metric_comparisons = needs_financial_facts and _comparison_requested(
            comparison_basis,
            comparison_candidates,
        )
        strategy = RetrievalStrategy(
            question_type=question_type,
            target_sections=target_sections,
            forms=forms,
            matched_rules=["strategy:llm_candidate"],
            confidence=confidence,
            needs_financial_facts=needs_financial_facts,
            needs_text_chunks=True,
            needs_metric_comparisons=needs_metric_comparisons,
            evidence_roles=_evidence_roles_for(
                question_type,
                target_sections,
                needs_metric_comparisons,
            ),
        )
        expansion = self._expansion_builder.build(
            query,
            intent,
            metrics,
            strategy,
            time=llm_time,
        )
        plan = RetrievalPlan(
            question_type=question_type,
            target_sections=target_sections,
            metric_keys=metric_keys,
            time_scope=llm_time.time_scope,
            comparison_basis=comparison_basis,
            comparison_candidates=comparison_candidates,
            default_comparison_basis=default_comparison_basis,
            ambiguities=[],
            forms=forms,
            dense_queries=expansion.dense_queries,
            dense_query_specs=expansion.dense_query_specs,
            lexical_queries=expansion.lexical_queries,
            rule_confidence=confidence,
            matched_rules=[
                "planner:llm_fallback",
                f"llm_fallback:{fallback_reason}",
                *metrics.matched_rules,
            ],
            planner_source="llm_fallback",
            confidence_breakdown={
                "intent_confidence": round(confidence, 2),
                "metric_confidence": round(confidence if metric_keys else 0.5, 2),
                "time_confidence": round(confidence, 2),
                "strategy_confidence": round(confidence, 2),
            },
            needs_financial_facts=needs_financial_facts,
            needs_text_chunks=True,
            needs_metric_comparisons=needs_metric_comparisons,
            evidence_roles=strategy.evidence_roles,
            requires_llm_fallback_reason=fallback_reason,
        )
        return self._validator.validate(
            plan,
            query,
            planner_source="llm_validated",
            validation_confidence=confidence,
        )

    def _get_llm_planner(self) -> LLMPlanner:
        if self._llm_planner is None:
            self._llm_planner = LLMQueryPlanner(self._settings)
        return self._llm_planner


def _contains_any(text: str, terms: tuple[str, ...]) -> bool:
    return any(_contains_term(text, term) for term in terms)


def _contains_term(text: str, term: str) -> bool:
    if not term:
        return False
    if term.isascii():
        return re.search(
            rf"(?<![a-z0-9]){re.escape(term.lower())}(?![a-z0-9])",
            text,
        ) is not None
    return term in text


def _is_liquidity_question(normalized_question: str) -> bool:
    return _contains_any(normalized_question, LIQUIDITY_TERMS) and not _contains_any(
        normalized_question,
        LIQUIDITY_NEGATIVE_TERMS,
    )


def _is_filing_summary_question(normalized_question: str) -> bool:
    if not _contains_any(normalized_question, SUMMARY_TERMS):
        return False
    return _contains_any(normalized_question, FILING_SUMMARY_CONTEXT_TERMS)


def _is_earnings_activity_question(normalized_question: str) -> bool:
    if _contains_any(normalized_question, EARNINGS_ACTIVITY_TERMS):
        return True
    return (
        re.search(
            r"(?<![a-z0-9])"
            r"(make|makes|made|making|earn|earns|earned|earning)"
            r"\s+(more|less)\s+money"
            r"(?![a-z0-9])",
            normalized_question,
        )
        is not None
    )


def _has_metric_comparative_signal(
    normalized_question: str,
    metric_keys: list[str] | None = None,
) -> bool:
    if not _contains_any(normalized_question, COMPARATIVE_METRIC_MODIFIER_TERMS):
        return False
    if re.search(
        r"(?<![a-z0-9])more\s+(about|detail|details|information|info|context)\b",
        normalized_question,
    ):
        return False

    metric_terms = _comparative_metric_terms(metric_keys)
    before_modifiers = (
        "more",
        "less",
        "higher",
        "lower",
        "increased",
        "decreased",
        "stronger",
        "weaker",
        "better",
        "worse",
    )
    after_modifiers = (
        "higher",
        "lower",
        "up",
        "down",
        "increased",
        "decreased",
        "stronger",
        "weaker",
        "better",
        "worse",
    )
    for term in sorted(metric_terms, key=len, reverse=True):
        term_pattern = _term_pattern(term)
        before_pattern = (
            rf"(?<![a-z0-9])(?:{'|'.join(before_modifiers)})\s+"
            rf"(?:[a-z0-9&'-]+\s+){{0,2}}{term_pattern}(?![a-z0-9])"
        )
        after_pattern = (
            rf"(?<![a-z0-9]){term_pattern}"
            rf"(?:\s+[a-z0-9&'-]+){{0,2}}\s+"
            rf"(?:{'|'.join(after_modifiers)})(?![a-z0-9])"
        )
        if re.search(before_pattern, normalized_question) or re.search(
            after_pattern,
            normalized_question,
        ):
            return True

    return False


def _comparative_metric_terms(metric_keys: list[str] | None) -> tuple[str, ...]:
    terms: list[str] = []
    keys = metric_keys or list(METRIC_RETRIEVAL_PROFILES)
    for metric_key in keys:
        profile = get_metric_profile(metric_key)
        if profile is None:
            continue
        terms.extend(profile.aliases)
        terms.extend(profile.strong_terms)
        terms.extend(profile.weak_terms)
        if metric_key in {"operating_income", "net_income"}:
            terms.extend(PROFITABILITY_TERMS)
        if metric_key == "net_income":
            terms.append("money")
        if metric_key in {"gross_margin", "operating_margin", "net_margin"}:
            terms.extend(MARGIN_JUDGMENT_TERMS)
    if not metric_keys:
        terms.extend(PROFITABILITY_TERMS)
        terms.extend(MARGIN_JUDGMENT_TERMS)
        terms.extend(SALES_ACTIVITY_TERMS)
        terms.append("money")
    return tuple(_dedupe([term for term in terms if term]))


def _term_pattern(term: str) -> str:
    return r"\s+".join(re.escape(part) for part in term.lower().split())


def _is_broad_company_growth_question(normalized_question: str, time_scope: str) -> bool:
    if time_scope != "comparison_trend":
        return False
    if not _contains_any(normalized_question, BROAD_COMPANY_GROWTH_TERMS):
        return False
    return not _contains_any(normalized_question, BROAD_COMPANY_GROWTH_EXCLUDED_SUBJECTS)


def _is_broad_performance_change_explanation(
    normalized_question: str,
    intent: IntentResult,
    time_scope: str,
) -> bool:
    if time_scope != "comparison_trend":
        return False
    if not intent.has_explanation_intent:
        return False
    if _contains_any(normalized_question, BROAD_COMPANY_GROWTH_EXCLUDED_SUBJECTS):
        return False
    return intent.has_performance_intent or _contains_any(
        normalized_question,
        VAGUE_COMPANY_CHANGE_TERMS,
    )


def _is_broad_performance_driver_question(
    normalized_question: str,
    intent: IntentResult,
) -> bool:
    if not intent.has_explanation_intent:
        return False
    if _contains_any(normalized_question, BROAD_COMPANY_GROWTH_EXCLUDED_SUBJECTS):
        return False
    return intent.has_performance_intent or _contains_any(
        normalized_question,
        BROAD_PERFORMANCE_DRIVER_SUBJECT_TERMS,
    )


def _is_vague_company_change_question(normalized_question: str, time_scope: str) -> bool:
    if time_scope != "comparison_trend":
        return False
    if not _contains_any(normalized_question, VAGUE_COMPANY_CHANGE_TERMS):
        return False
    if not _contains_any(normalized_question, COMPARISON_CONTEXT_TERMS):
        return False
    return not _contains_any(normalized_question, BROAD_COMPANY_GROWTH_EXCLUDED_SUBJECTS)


def _is_vague_company_performance_question(
    normalized_question: str,
    *,
    has_performance_intent: bool,
) -> bool:
    if not has_performance_intent:
        return False
    if _contains_any(normalized_question, BROAD_COMPANY_GROWTH_EXCLUDED_SUBJECTS):
        return False
    return _contains_any(normalized_question, QUARTERLY_BASIS_TERMS) or _contains_any(
        normalized_question,
        LATEST_TERMS,
    )


def _dedupe(items: list[str] | tuple[str, ...] | Any) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        if item not in seen:
            seen.add(item)
            result.append(item)
    return result


def _detect_comparison_basis(normalized_question: str) -> str:
    if _contains_any(normalized_question, QUARTERLY_BASIS_TERMS):
        return "latest_quarter_yoy"
    if _contains_any(normalized_question, YTD_BASIS_TERMS):
        return "latest_ytd_yoy"
    if _contains_any(normalized_question, FY_BASIS_TERMS):
        return "latest_fy_yoy"
    return "ambiguous"


def _detect_growth_acceleration_basis(
    normalized_question: str,
) -> tuple[str, list[str], str, str | None]:
    if _contains_any(normalized_question, FY_BASIS_TERMS):
        return (
            "latest_fy_yoy",
            ["latest_fy_yoy", "previous_fy_yoy"],
            "latest_fy_yoy",
            None,
        )
    if _contains_any(normalized_question, YTD_BASIS_TERMS):
        return (
            "latest_ytd_yoy",
            ["latest_ytd_yoy", "previous_ytd_yoy"],
            "latest_ytd_yoy",
            None,
        )
    ambiguity = None
    if not _contains_any(normalized_question, QUARTERLY_BASIS_TERMS):
        ambiguity = (
            "Interpreted growth acceleration as latest quarter year-over-year growth "
            "versus prior quarter year-over-year growth because no period was specified."
        )
    return (
        "latest_quarter_yoy",
        ["latest_quarter_yoy", "previous_quarter_yoy"],
        "latest_quarter_yoy",
        ambiguity,
    )


def _default_comparison_basis_for_metrics(metric_keys: list[str]) -> str:
    for metric_key in metric_keys:
        profile = get_metric_profile(metric_key)
        if profile is not None:
            return profile.default_comparison_basis
    return "latest_quarter_yoy"


def _preferred_form_for_comparison_basis(comparison_basis: str | None) -> str | None:
    if comparison_basis in {
        "latest_quarter_yoy",
        "previous_quarter_yoy",
        "latest_ytd_yoy",
        "previous_ytd_yoy",
    }:
        return "10-Q"
    if comparison_basis in {"latest_fy_yoy", "previous_fy_yoy"}:
        return "10-K"
    return None


def _build_metric_lexical_queries(metric_keys: list[str]) -> list[str]:
    queries: list[str] = []
    for metric_key in metric_keys:
        profile = get_metric_profile(metric_key)
        if profile is not None:
            queries.extend(profile.lexical_queries)
            continue
        queries.append(METRIC_LABELS.get(metric_key, metric_key).lower())
    return queries


def _overall_confidence(
    confidence: dict[str, float],
    *,
    has_metric: bool,
    metric_optional: bool,
) -> float:
    metric_confidence = confidence.get("metric_confidence", 0.45)
    if not has_metric and metric_optional:
        metric_confidence = max(metric_confidence, 0.8)
    return (
        0.2 * confidence.get("intent_confidence", 0.45)
        + 0.25 * metric_confidence
        + 0.2 * confidence.get("time_confidence", 0.55)
        + 0.15 * confidence.get("strategy_confidence", 0.55)
        + 0.2 * confidence.get("validation_confidence", 1.0)
    )


def _llm_fallback_reason(plan: RetrievalPlan, *, threshold: float) -> str | None:
    if "validation:time_basis_conflict" in plan.matched_rules:
        return "time_basis_conflict"
    if plan.rule_confidence < threshold:
        if plan.question_type == "prose" and not plan.metric_keys:
            return "ambiguous_intent"
        return "low_confidence"
    return None


def _as_str_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item) for item in value if str(item).strip()]
    if isinstance(value, tuple):
        return [str(item) for item in value if str(item).strip()]
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    return []


def _coerce_confidence(value: Any, *, fallback: float) -> float:
    try:
        return max(0.0, min(1.0, float(value)))
    except (TypeError, ValueError):
        return fallback

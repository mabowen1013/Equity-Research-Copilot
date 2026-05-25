from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class MetricRetrievalProfile:
    lexical_queries: tuple[str, ...]
    strong_terms: tuple[str, ...]
    statement_terms: tuple[str, ...]
    weak_terms: tuple[str, ...]
    aliases: tuple[str, ...] = ()
    negative_terms: tuple[str, ...] = ()
    default_comparison_basis: str = "latest_quarter_yoy"
    preferred_sections: tuple[str, ...] = ()
    fact_tags: tuple[str, ...] = ()
    formula_metadata: dict[str, str] | None = None


METRIC_RETRIEVAL_PROFILES: dict[str, MetricRetrievalProfile] = {
    "revenue": MetricRetrievalProfile(
        lexical_queries=(
            '"total net sales"',
            '"net sales"',
            '"revenue growth"',
            '"net sales increased"',
            '"net sales decreased"',
            '"total net sales increased"',
            '"total net sales decreased"',
            '"revenue from contract"',
            '"condensed consolidated statements of operations"',
            '"consolidated statements of operations"',
            '"three months ended" "net sales"',
            '"six months ended" "net sales"',
            '"year ended" "net sales"',
        ),
        strong_terms=("total net sales", "net sales", "revenue from contract"),
        statement_terms=(
            "condensed consolidated statements of operations",
            "consolidated statements of operations",
            "statements of operations",
            "three months ended",
            "six months ended",
            "year ended",
        ),
        weak_terms=("revenue",),
        aliases=(
            "revenue",
            "revenues",
            "sales",
            "net sales",
            "total sales",
            "total net sales",
            "total revenue",
            "top line",
            "top-line",
            "topline",
            "turnover",
            "收入",
            "营收",
            "销售额",
        ),
        negative_terms=(
            "deferred revenue",
            "revenue recognition",
            "revenue exposure",
            "foreign currency exposure",
            "remaining performance obligations",
        ),
        preferred_sections=("Financial Statements", "Management's Discussion and Analysis"),
    ),
    "gross_profit": MetricRetrievalProfile(
        lexical_queries=(
            '"gross profit"',
            '"gross profit increased"',
            '"gross profit decreased"',
            '"gross margin"',
            '"statements of operations"',
            '"net sales" "cost of sales"',
        ),
        strong_terms=("gross profit",),
        statement_terms=("statements of operations", "net sales", "cost of sales"),
        weak_terms=("gross profit", "gross margin"),
        aliases=("gross profit", "毛利"),
        preferred_sections=("Financial Statements",),
    ),
    "operating_income": MetricRetrievalProfile(
        lexical_queries=(
            '"operating income"',
            '"operating income increased"',
            '"operating income decreased"',
            '"income from operations"',
            '"statements of operations"',
        ),
        strong_terms=("operating income", "income from operations"),
        statement_terms=("statements of operations", "operating expenses"),
        weak_terms=("operating income",),
        aliases=("operating income", "income from operations", "营业利润"),
        preferred_sections=("Financial Statements", "Management's Discussion and Analysis"),
    ),
    "net_income": MetricRetrievalProfile(
        lexical_queries=(
            '"net income"',
            '"net income increased"',
            '"net income decreased"',
            '"net earnings"',
            '"statements of operations"',
        ),
        strong_terms=("net income", "net earnings"),
        statement_terms=("statements of operations", "earnings per share"),
        weak_terms=("net income", "earnings"),
        aliases=("net income", "net earnings", "earnings", "profit", "profits", "bottom line", "净利润"),
        preferred_sections=("Financial Statements", "Management's Discussion and Analysis"),
    ),
    "operating_cash_flow": MetricRetrievalProfile(
        lexical_queries=(
            '"net cash provided by operating activities"',
            '"cash provided by operating activities"',
            '"operating activities"',
            '"statements of cash flows"',
        ),
        strong_terms=(
            "net cash provided by operating activities",
            "cash provided by operating activities",
        ),
        statement_terms=("statements of cash flows", "operating activities"),
        weak_terms=("operating cash flow", "cash flow"),
        aliases=(
            "operating cash flow",
            "cash from operations",
            "cash provided by operations",
            "cash provided by operating activities",
            "cfo",
            "经营现金流",
        ),
        preferred_sections=("Financial Statements", "Liquidity", "Management's Discussion and Analysis"),
    ),
    "capital_expenditures": MetricRetrievalProfile(
        lexical_queries=(
            '"capital expenditures"',
            '"payments for acquisition of property plant and equipment"',
            '"payments to acquire property plant and equipment"',
            '"property, plant and equipment"',
            '"statements of cash flows"',
        ),
        strong_terms=(
            "capital expenditures",
            "payments for acquisition of property plant and equipment",
            "payments to acquire property plant and equipment",
        ),
        statement_terms=("statements of cash flows", "property, plant and equipment"),
        weak_terms=("capital expenditures", "capex"),
        aliases=("capex", "capital expenditure", "capital expenditures", "资本开支"),
        preferred_sections=("Financial Statements", "Liquidity", "Management's Discussion and Analysis"),
    ),
    "free_cash_flow": MetricRetrievalProfile(
        lexical_queries=(
            '"free cash flow"',
            '"net cash provided by operating activities"',
            '"capital expenditures"',
            '"payments for acquisition of property plant and equipment"',
            '"statements of cash flows"',
        ),
        strong_terms=("free cash flow",),
        statement_terms=(
            "net cash provided by operating activities",
            "capital expenditures",
            "statements of cash flows",
        ),
        weak_terms=("free cash flow", "cash flow"),
        aliases=("free cash flow", "fcf", "自由现金流"),
        preferred_sections=("Liquidity", "Financial Statements", "Management's Discussion and Analysis"),
        formula_metadata={
            "formula": "operating_cash_flow - capital_expenditures",
            "requires": "operating_cash_flow, capital_expenditures",
        },
    ),
    "gross_margin": MetricRetrievalProfile(
        lexical_queries=(
            '"gross margin"',
            '"gross margin increased"',
            '"gross margin decreased"',
            '"gross profit"',
            '"net sales"',
            '"cost of sales"',
        ),
        strong_terms=("gross margin", "gross profit"),
        statement_terms=("net sales", "cost of sales", "statements of operations"),
        weak_terms=("gross margin",),
        aliases=("gross margin", "gross margins", "gross margin percentage", "毛利率"),
        preferred_sections=("Financial Statements", "Management's Discussion and Analysis"),
    ),
    "operating_margin": MetricRetrievalProfile(
        lexical_queries=(
            '"operating margin"',
            '"operating income"',
            '"income from operations"',
            '"net sales"',
            '"statements of operations"',
        ),
        strong_terms=("operating margin", "operating income", "income from operations"),
        statement_terms=("net sales", "statements of operations"),
        weak_terms=("operating margin",),
        aliases=("operating margin", "营业利润率"),
        preferred_sections=("Financial Statements", "Management's Discussion and Analysis"),
    ),
    "net_margin": MetricRetrievalProfile(
        lexical_queries=(
            '"net margin"',
            '"net income"',
            '"net earnings"',
            '"net sales"',
            '"statements of operations"',
        ),
        strong_terms=("net margin", "net income", "net earnings"),
        statement_terms=("net sales", "statements of operations"),
        weak_terms=("net margin",),
        aliases=("net margin", "净利率"),
        preferred_sections=("Financial Statements", "Management's Discussion and Analysis"),
    ),
}


def get_metric_profile(metric_key: str) -> MetricRetrievalProfile | None:
    return METRIC_RETRIEVAL_PROFILES.get(metric_key)

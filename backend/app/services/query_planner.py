from __future__ import annotations

from collections import OrderedDict
from dataclasses import asdict, dataclass, field, replace
import json
import re
from threading import Lock
from typing import Any, Protocol

from app.core import Settings, get_settings
from app.services.metric_profiles import METRIC_RETRIEVAL_PROFILES, get_metric_profile
from app.services.openai_client import get_openai_client

LLM_RESPONSE_CACHE_MAX_ENTRIES = 256

_llm_response_cache: OrderedDict[tuple[str, str, str], Any] = OrderedDict()
_llm_response_cache_lock = Lock()


def _llm_cache_get(key: tuple[str, str, str]) -> Any | None:
    with _llm_response_cache_lock:
        value = _llm_response_cache.get(key)
        if value is not None:
            _llm_response_cache.move_to_end(key)
        return value


def _llm_cache_put(key: tuple[str, str, str], value: Any) -> None:
    with _llm_response_cache_lock:
        _llm_response_cache[key] = value
        _llm_response_cache.move_to_end(key)
        while len(_llm_response_cache) > LLM_RESPONSE_CACHE_MAX_ENTRIES:
            _llm_response_cache.popitem(last=False)


def clear_llm_response_cache() -> None:
    with _llm_response_cache_lock:
        _llm_response_cache.clear()


VALID_FORMS = {
    "10-K",  # 年报；当用户问最新年报、完整财年表现、年度风险因素时使用。
    "10-Q",  # 季报；当用户问最新季度、年初至今、或中期财务表现时使用。
    "8-K",  # 临时报告；当用户问重大事件、公告、业绩发布等事件驱动信息时使用。
}
VALID_QUESTION_TYPES = {
    "risk",  # 风险因素类问题；识别法律、监管、业务、运营等风险相关提问。
    "liquidity",  # 流动性类问题；识别现金生成、资本资源、资金 runway、融资能力等提问。
    "filing_summary",  # 文件摘要类问题；识别“总结这份 filing / 有什么重点”这类宽泛请求。
    "management_discussion",  # 管理层讨论类问题；识别需要 MD&A 解释、原因、驱动因素的提问。
    "growth_acceleration",  # 增长加速类问题；识别增长是在加速还是放缓的跨期比较。
    "broad_comparison",  # 宽泛比较类问题；识别跨期间、公司、产品、分部的整体对比。
    "performance_overview",  # 经营表现概览；识别“公司表现如何”这类需要核心损益指标的问题。
    "performance_judgment",  # 经营表现判断；识别“好不好、强不强、是否改善”等评价型问题。
    "mixed",  # 混合类问题；同时需要财务数字和文字解释、原因、管理层表述。
    "trend",  # 趋势类问题；识别增长、下降、同比变化、方向性变化等时间序列提问。
    "metric",  # 指标查询类问题；识别某个具体数字、比例、报表行项目的点查询。
    "comparison",  # 聚焦比较类问题；识别明确期间、指标、章节、或同行之间的对比。
    "prose",  # 文本检索类问题；问题主要不是财务事实查询时，用宽泛文本证据回答。
}
VALID_TIME_SCOPES = {
    "latest",  # 最新时间范围；用户明确或强烈暗示要最近一期 filing / 最近报告期。
    "comparison_trend",  # 比较趋势范围；用户问变化、增长、趋势、加速、或与前期相比。
    "unspecified",  # 未指定时间范围；query 中无法可靠推断应使用哪个时间区间。
}
VALID_PERIOD_KINDS = {
    "quarter",  # 单季度口径；通常对应 three months ended。
    "ytd",  # 年初至今口径；通常对应 six/nine months ended。
    "fy",  # 完整财年口径；通常对应 year ended。
    "instant",  # 资产负债表时点指标。
    "unspecified",  # query 中没有足够信息判断期间口径。
}
VALID_TARGET_PERIODS = {
    "latest",  # 最新已报告期间。
    "previous",  # 上一个已报告期间。
    "specified",  # 用户指定了具体期间或日期。
    "unspecified",
}
VALID_DURATION_CLASSES = {
    "quarter",
    "ytd",
    "fy",
    "instant",
}
VALID_TARGET_SECTIONS = {
    "Financial Statements",  # 财务报表章节；用于取报表数字、附注式行项目、主要财务事实。
    "Management's Discussion and Analysis",  # MD&A 章节；用于取管理层解释、驱动因素、经营背景。
    "Risk Factors",  # 风险因素章节；用于取 Item 1A 或风险相关文本证据。
    "Liquidity",  # 流动性章节；用于取 liquidity and capital resources、现金需求、融资背景。
    "Cash Flows",  # 现金流章节；用于取经营、投资、融资现金流相关证据。
}
VALID_COMPARISON_BASES = {
    "none",  # 不需要比较；问题是单点查询或摘要，不要求同比、环比、跨期变化。
    "ambiguous",  # 比较口径不明确；用户问变化/增长，但没说季度、YTD、还是全年口径。
    "latest_quarter_yoy",  # 最新季度同比；最近一个季度对比去年同期季度。
    "previous_quarter_yoy",  # 上一个已报告季度同比；前一季度对比其去年同期季度。
    "latest_ytd_yoy",  # 最新年初至今同比；最近 YTD 期间对比去年同期 YTD。
    "previous_ytd_yoy",  # 上一个 YTD 同比；前一个 YTD 期间对比其去年同期 YTD。
    "latest_fy_yoy",  # 最新财年同比；最近完整财年对比上一完整财年。
    "previous_fy_yoy",  # 上一个财年同比；前一完整财年对比再前一完整财年。
}
VALID_EVIDENCE_ROLES = {
    "metric_comparisons",  # 需要结构化指标比较；用于回答同比、跨期变化、趋势问题。
    "primary_financial_statement_chunks",  # 需要财务报表原文块；用于支撑数字、报表行项目。
    "mda_explanation_chunks",  # 需要 MD&A 解释块；用于说明原因、驱动因素、管理层表述。
    "segment_or_product_breakdown_chunks",  # 需要分部/产品拆分块；用于解释业务线或地区表现。
    "risk_factor_chunks",  # 需要风险因素块；用于风险相关问题的主要文本证据。
    "annual_context_chunks",  # 需要年报背景；即使主问题不是只问年报，也要补年度上下文。
}
VALID_LLM_PLAN_FIELDS = {
    "question_type",  # LLM 识别出的语义意图，取值来自 VALID_QUESTION_TYPES。
    "target_sections",  # 值得检索的 filing 章节，用作文本证据来源。
    "metric_keys",  # 归一化后的指标 key，用于后续财务事实检索。
    "time_scope",  # 粗粒度时间意图，取值来自 VALID_TIME_SCOPES。
    "period_kind",  # 事实期间口径，如 quarter/ytd/fy；用于过滤 XBRL facts。
    "target_period",  # latest/previous/specified；用于决定 scope 和排序。
    "duration_class",  # 与 financial fact duration_class 对齐的过滤字段。
    "comparison_basis",  # 主要比较口径，取值来自 VALID_COMPARISON_BASES。
    "comparison_candidates",  # 当比较口径不明确时，可尝试的候选比较口径。
    "default_comparison_basis",  # 多个候选口径存在时，后端优先采用的默认口径。
    "ambiguities",  # query 中仍未解决的歧义或假设，给后续链路参考。
    "forms",  # 当用户明确要求某类 filing 时，用来硬约束检索范围。
    "allowed_forms",  # 当没有明确 form 要求时，retrieval 可搜索的 filing 类型。
    "preferred_forms",  # 不硬约束检索时，用来提高某类 filing 的优先级。
    "reasoning_summary",  # 可选的 LLM 理由摘要；schema 接受，但 RetrievalPlan 不保存。
    "dense_query_specs",  # 单次调用模式下随槽位一起返回的 dense 检索 query；无效时回退到独立 rewriter 调用。
}
PERFORMANCE_OVERVIEW_METRICS = [
    "revenue",  # 收入；经营表现概览中的核心 top-line 指标。
    "gross_margin",  # 毛利率；用于观察定价、成本压力和毛利水平。
    "operating_income",  # 营业利润；用于观察主营业务盈利能力。
    "net_income",  # 净利润；用于观察扣除全部费用、税费、其他项目后的底线利润。
]
SUMMARY_METRICS = [
    *PERFORMANCE_OVERVIEW_METRICS,  # filing 摘要默认包含的核心损益指标。
    "operating_cash_flow",  # 经营现金流；经营活动产生的现金。
    "free_cash_flow",  # 自由现金流；经营现金流扣除资本开支后的现金。
]
LIQUIDITY_METRICS = [
    "operating_cash_flow",  # 流动性问题的主要现金生成指标。
    "free_cash_flow",  # 扣除资本开支后的可用现金，用于判断资金灵活性。
]
GROWTH_METRICS = [
    "revenue",  # 收入增长信号。
    "operating_income",  # 营业利润增长信号。
    "net_income",  # 净利润增长信号。
]
MAX_DENSE_QUERIES = 6
MAX_LLM_DENSE_QUERY_ROLES = MAX_DENSE_QUERIES - 1
MAX_LEXICAL_QUERIES = 14
LEXICAL_COMPARISON_TERMS = {
    "latest_quarter_yoy": (
        '"three months ended"',
        '"compared to"',
        '"prior year"',
    ),
    "previous_quarter_yoy": (
        '"three months ended"',
        '"compared to"',
        '"prior year"',
    ),
    "latest_ytd_yoy": (
        '"six months ended"',
        '"nine months ended"',
        '"compared to"',
        '"prior year"',
    ),
    "previous_ytd_yoy": (
        '"six months ended"',
        '"nine months ended"',
        '"compared to"',
        '"prior year"',
    ),
    "latest_fy_yoy": (
        '"year ended"',
        '"fiscal year"',
        '"compared to"',
        '"prior year"',
    ),
    "previous_fy_yoy": (
        '"year ended"',
        '"fiscal year"',
        '"compared to"',
        '"prior year"',
    ),
    "duration_quarter": (
        '"three months ended"',
    ),
    "duration_ytd": (
        '"six months ended"',
        '"nine months ended"',
        '"year to date"',
    ),
    "duration_fy": (
        '"year ended"',
        '"fiscal year"',
    ),
    "duration_instant": (
        '"as of"',
        '"period end"',
        '"balance sheet"',
    ),
    "ambiguous": (
        '"compared to"',
    ),
    "none": (),
}
LEXICAL_SECTION_TERMS = {
    "Financial Statements": (
        '"consolidated statements of operations"',
        '"statements of operations"',
        '"financial statements"',
    ),
    "Management's Discussion and Analysis": (
        '"results of operations"',
        '"management discussion and analysis"',
    ),
    "Cash Flows": (
        '"statements of cash flows"',
        '"operating activities"',
    ),
    "Liquidity": (
        '"liquidity and capital resources"',
        '"cash requirements"',
    ),
    "Risk Factors": (
        '"risk factors"',
        '"item 1a"',
    ),
}
DENSE_WEIGHT_PRIMARY = 1.0
DENSE_WEIGHT_FINANCIAL_STATEMENT = 0.95
DENSE_WEIGHT_SUPPORTING_CONTEXT = 0.85
DENSE_WEIGHT_ORIGINAL_QUERY = 0.45
VALID_LLM_DENSE_QUERY_ROLES = {
    "slot",
    "financial_statement",
    "cash_flow",
    "liquidity",
    "mda",
    "risk",
}
DENSE_ROLE_WEIGHTS = {
    "slot": DENSE_WEIGHT_PRIMARY,
    "financial_statement": DENSE_WEIGHT_FINANCIAL_STATEMENT,
    "cash_flow": DENSE_WEIGHT_PRIMARY,
    "liquidity": DENSE_WEIGHT_SUPPORTING_CONTEXT,
    "mda": DENSE_WEIGHT_SUPPORTING_CONTEXT,
    "risk": DENSE_WEIGHT_PRIMARY,
    "original": DENSE_WEIGHT_ORIGINAL_QUERY,
}
SECTION_DENSE_CONTEXT = {
    "Financial Statements": (
        "financial statements consolidated statements of operations statements of cash flows"
    ),
    "Management's Discussion and Analysis": (
        "management discussion analysis results of operations liquidity capital resources"
    ),
    "Risk Factors": "risk factors item 1a business operational financial regulatory risks",
    "Liquidity": "liquidity and capital resources cash requirements operating activities",
    "Cash Flows": "statement of cash flows operating investing financing activities",
}
QUESTION_TYPE_DENSE_CONTEXT = {
    "metric": "latest filing reported amount financial statement line item",
    "trend": "year over year trend compared with prior period",
    "mixed": "financial statement amount management discussion reasons drivers",
    "performance_overview": "latest quarter financial performance results of operations",
    "liquidity": "cash generation liquidity capital resources cash flows",
}
COMPARISON_DENSE_CONTEXT = {
    "latest_quarter_yoy": "latest quarter three months ended year over year compared to prior year",
    "previous_quarter_yoy": "previous quarter three months ended year over year",
    "latest_ytd_yoy": "latest year to date six months nine months ended compared to prior year",
    "previous_ytd_yoy": "previous year to date compared to prior year",
    "latest_fy_yoy": "latest fiscal year year ended compared to prior fiscal year annual report",
    "previous_fy_yoy": "previous fiscal year year ended compared to prior fiscal year annual report",
    "ambiguous": "quarterly year to date annual year over year trend",
}
DENSE_REWRITER_SECTION_TERMS = {
    "Financial Statements": [
        "financial statements",
        "consolidated statements of operations",
        "statements of cash flows",
        "reported amounts",
        "line items",
    ],
    "Management's Discussion and Analysis": [
        "management discussion and analysis",
        "results of operations",
        "drivers",
        "reasons",
        "primarily due to",
    ],
    "Risk Factors": ["risk factors", "item 1a", "business risks", "regulatory risks"],
    "Liquidity": [
        "liquidity and capital resources",
        "cash requirements",
        "working capital",
        "funding capacity",
    ],
    "Cash Flows": [
        "statements of cash flows",
        "operating activities",
        "investing activities",
        "financing activities",
    ],
}
DENSE_REWRITER_COMPARISON_TERMS = {
    "none": [],
    "ambiguous": ["year over year", "quarterly", "year to date", "annual", "trend"],
    "latest_quarter_yoy": [
        "latest quarter",
        "three months ended",
        "year over year",
        "compared to prior year",
        "prior year quarter",
    ],
    "previous_quarter_yoy": [
        "previous quarter",
        "three months ended",
        "year over year",
        "compared to prior year",
        "prior year quarter",
    ],
    "latest_ytd_yoy": [
        "latest year to date",
        "six months ended",
        "nine months ended",
        "year over year",
        "compared to prior year",
    ],
    "previous_ytd_yoy": [
        "previous year to date",
        "six months ended",
        "nine months ended",
        "year over year",
        "compared to prior year",
    ],
    "latest_fy_yoy": [
        "latest fiscal year",
        "year ended",
        "year over year",
        "compared to prior fiscal year",
    ],
    "previous_fy_yoy": [
        "previous fiscal year",
        "year ended",
        "year over year",
        "compared to prior fiscal year",
    ],
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
    matched_rules: list[str]
    period_kind: str | None = None
    target_period: str | None = None
    duration_class: str | None = None
    allowed_forms: list[str] = field(default_factory=list)
    preferred_forms: list[str] = field(default_factory=list)
    dense_query_specs: list[dict[str, Any]] = field(default_factory=list)
    lexical_query_specs: list[dict[str, Any]] = field(default_factory=list)
    planner_source: str = "llm_validated"
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
    form_type: str | None = None
    section: str | None = None


class LLMPlanner(Protocol):
    def plan_candidate(self, question: str) -> dict[str, Any]:
        """Return a raw JSON-compatible candidate plan."""


class DenseQueryRewriter(Protocol):
    def rewrite(
        self,
        *,
        question: str,
        plan: RetrievalPlan,
        requested_roles: list[str],
    ) -> list[dict[str, Any]]:
        """Return dense embedding query specs for validated retrieval roles."""


class QueryNormalizer:
    def normalize(
        self,
        question: str,
        *,
        form_type: str | None = None,
        section: str | None = None,
    ) -> NormalizedQuery:
        return NormalizedQuery(
            original=" ".join(question.split()),
            form_type=form_type.strip().upper() if form_type and form_type.strip() else None,
            section=section.strip() if section and section.strip() else None,
        )


class LLMQueryPlanner:
    allowed_fields = VALID_LLM_PLAN_FIELDS

    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()

    def plan_candidate(self, question: str) -> dict[str, Any]:
        api_key = self._settings.openai_api_key
        if api_key is None or not api_key.get_secret_value().strip():
            raise RuntimeError("OPENAI_API_KEY must be configured for LLM query planning.")

        cache_key = ("plan_candidate", self._settings.query_planner_llm_model, question)
        cached = _llm_cache_get(cache_key)
        if cached is not None:
            return json.loads(cached)

        try:
            client = get_openai_client(
                api_key.get_secret_value(),
                timeout=self._settings.query_planner_llm_timeout_seconds,
                max_retries=self._settings.query_planner_llm_max_retries,
            )
        except ImportError as exc:
            raise RuntimeError("The openai package must be installed for LLM query planning.") from exc

        response = client.chat.completions.create(
            model=self._settings.query_planner_llm_model,
            temperature=0,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": _llm_planner_system_prompt()},
                {"role": "user", "content": question},
            ],
        )
        content = response.choices[0].message.content or "{}"
        candidate = _parse_llm_json(content)
        unknown_fields = set(candidate) - self.allowed_fields
        if unknown_fields:
            raise ValueError(f"LLM planner returned unsupported fields: {sorted(unknown_fields)}")
        _llm_cache_put(cache_key, json.dumps(candidate, ensure_ascii=False))
        return candidate


class LLMDenseQueryRewriter:
    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()

    def rewrite(
        self,
        *,
        question: str,
        plan: RetrievalPlan,
        requested_roles: list[str],
    ) -> list[dict[str, Any]]:
        api_key = self._settings.openai_api_key
        if api_key is None or not api_key.get_secret_value().strip():
            raise RuntimeError("OPENAI_API_KEY must be configured for LLM dense query rewriting.")

        payload_text = json.dumps(
            _llm_dense_query_rewriter_payload(
                question=question,
                plan=plan,
                requested_roles=requested_roles,
            ),
            ensure_ascii=False,
            sort_keys=True,
        )
        cache_key = (
            "dense_query_rewrite",
            self._settings.query_planner_llm_model,
            payload_text,
        )
        cached = _llm_cache_get(cache_key)
        if cached is not None:
            return json.loads(cached)

        try:
            client = get_openai_client(
                api_key.get_secret_value(),
                timeout=self._settings.query_planner_llm_timeout_seconds,
                max_retries=self._settings.query_planner_llm_max_retries,
            )
        except ImportError as exc:
            raise RuntimeError(
                "The openai package must be installed for LLM dense query rewriting."
            ) from exc

        response = client.chat.completions.create(
            model=self._settings.query_planner_llm_model,
            temperature=0,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": _llm_dense_query_rewriter_system_prompt()},
                {"role": "user", "content": payload_text},
            ],
        )
        content = response.choices[0].message.content or "{}"
        parsed = _parse_llm_json(content)
        specs = parsed.get("dense_query_specs")
        if not isinstance(specs, list):
            raise ValueError("LLM dense query rewriter must return dense_query_specs.")
        _llm_cache_put(cache_key, json.dumps(specs, ensure_ascii=False))
        return specs


@dataclass(frozen=True)
class CompiledQueries:
    dense_queries: list[str]
    dense_query_specs: list[dict[str, Any]]
    lexical_queries: list[str]
    lexical_query_specs: list[dict[str, Any]]


class QueryCompiler:
    def compile(
        self,
        query: NormalizedQuery,
        *,
        question_type: str,
        target_sections: list[str],
        metric_keys: list[str],
        time_scope: str,
        comparison_basis: str,
        needs_metric_comparisons: bool,
        duration_class: str | None = None,
    ) -> CompiledQueries:
        # 先添加上硬编码的dense query fallback，后续会替换成 LLM 生成的版本
        metric_phrases = _metric_dense_phrases(metric_keys)
        section_phrases = _section_dense_phrases(target_sections)
        comparison_phrase = COMPARISON_DENSE_CONTEXT.get(comparison_basis, "")
        period_phrase = " ".join(_period_terms_for_duration(duration_class))
        question_type_phrase = QUESTION_TYPE_DENSE_CONTEXT.get(question_type, "")
        time_phrase = _time_dense_context(time_scope)

        specs: list[dict[str, Any]] = []
        self._add_spec(
            specs,
            role="slot",
            text=_join_terms(
                [
                    question_type_phrase,
                    time_phrase,
                    comparison_phrase,
                    period_phrase,
                    *section_phrases,
                    *metric_phrases,
                ],
                max_words=34,
            ),
            weight=DENSE_WEIGHT_PRIMARY,
        )
        if metric_keys or any(
            section in target_sections for section in {"Financial Statements", "Cash Flows"}
        ):
            self._add_spec(
                specs,
                role="financial_statement",
                text=_join_terms(
                    [
                        "financial statements consolidated statements of operations statements of cash flows line item reported amount",
                        comparison_phrase,
                        period_phrase,
                        *metric_phrases,
                    ],
                    max_words=32,
                ),
                weight=DENSE_WEIGHT_FINANCIAL_STATEMENT,
            )
        if "Cash Flows" in target_sections or any(
            metric in metric_keys
            for metric in {"operating_cash_flow", "free_cash_flow", "capital_expenditures"}
        ):
            self._add_spec(
                specs,
                role="cash_flow",
                text=_join_terms(
                    [
                        "statement of cash flows operating investing financing activities cash generation",
                        *metric_phrases,
                    ],
                    max_words=30,
                ),
                weight=DENSE_WEIGHT_PRIMARY,
            )
        if "Liquidity" in target_sections or any(
            metric in metric_keys for metric in {"operating_cash_flow", "free_cash_flow"}
        ):
            self._add_spec(
                specs,
                role="liquidity",
                text=_join_terms(
                    [
                        "liquidity and capital resources cash requirements cash generated operating activities",
                        *metric_phrases,
                    ],
                    max_words=30,
                ),
                weight=DENSE_WEIGHT_SUPPORTING_CONTEXT,
            )
        if (
            "Management's Discussion and Analysis" in target_sections
            or question_type in {"mixed", "performance_overview", "performance_judgment"}
        ):
            self._add_spec(
                specs,
                role="mda",
                text=_join_terms(
                    [
                        "management discussion analysis results of operations reasons drivers primarily due to higher lower",
                        comparison_phrase,
                        *metric_phrases,
                    ],
                    max_words=32,
                ),
                weight=DENSE_WEIGHT_SUPPORTING_CONTEXT,
            )
        if "Risk Factors" in target_sections or question_type == "risk":
            self._add_spec(
                specs,
                role="risk",
                text="latest annual quarterly report risk factors item 1a business operational financial regulatory risks",
                weight=DENSE_WEIGHT_PRIMARY,
            )
        self._add_spec(
            specs,
            role="original",
            text=query.original,
            weight=DENSE_WEIGHT_ORIGINAL_QUERY,
        )

        dense_query_specs = specs[:MAX_DENSE_QUERIES]
        dense_queries = [spec["text"] for spec in dense_query_specs]
        lexical_query_specs = _compile_lexical_query_specs(
            metric_keys=metric_keys,
            target_sections=target_sections,
            comparison_basis=(
                comparison_basis
                if needs_metric_comparisons
                else _lexical_basis_for_duration(duration_class)
            ),
            question_type=question_type,
        )
        lexical_queries = _flatten_lexical_query_specs(lexical_query_specs)
        if not lexical_queries:
            lexical_queries = [query.original]
            lexical_query_specs = [
                {"role": "original", "queries": lexical_queries, "weight": 0.45}
            ]

        return CompiledQueries(
            dense_queries=dense_queries,
            dense_query_specs=dense_query_specs,
            lexical_queries=lexical_queries,
            lexical_query_specs=lexical_query_specs,
        )

    def _add_spec(
        self,
        specs: list[dict[str, Any]],
        *,
        role: str,
        text: str,
        weight: float,
    ) -> None:
        normalized_text = " ".join(text.split())
        if not normalized_text:
            return
        if any(spec["text"] == normalized_text for spec in specs):
            return
        specs.append(
            {
                "role": _normalize_role(role),
                "text": normalized_text,
                "weight": _coerce_weight(weight),
            }
        )


class PlanValidator:
    def __init__(self, compiler: QueryCompiler | None = None) -> None:
        self._compiler = compiler or QueryCompiler()

    def validate(
        self,
        candidate: dict[str, Any],
        query: NormalizedQuery,
        *,
        planner_source: str = "llm_validated",
    ) -> RetrievalPlan:
        unknown_fields = set(candidate) - VALID_LLM_PLAN_FIELDS
        if unknown_fields:
            raise ValueError(f"Planner returned unsupported fields: {sorted(unknown_fields)}")

        question_type = _validated_scalar(
            candidate.get("question_type"),
            allowed=VALID_QUESTION_TYPES,
            fallback="prose",
        )
        target_sections = _validated_list(
            candidate.get("target_sections"),
            allowed=VALID_TARGET_SECTIONS,
        )
        query_section = _validated_query_section(query.section)
        if query_section and query_section not in target_sections:
            target_sections.append(query_section)

        metric_keys = _validated_list(
            candidate.get("metric_keys"),
            allowed=set(METRIC_RETRIEVAL_PROFILES),
        )
        if not metric_keys:
            metric_keys = _default_metric_keys_for_question_type(question_type)
        time_scope = _validated_scalar(
            candidate.get("time_scope"),
            allowed=VALID_TIME_SCOPES,
            fallback="unspecified",
        )
        comparison_basis = _validated_scalar(
            candidate.get("comparison_basis"),
            allowed=VALID_COMPARISON_BASES,
            fallback="none",
        )
        comparison_candidates = _validated_list(
            candidate.get("comparison_candidates"),
            allowed=VALID_COMPARISON_BASES - {"none", "ambiguous"},
        )
        if metric_keys and comparison_basis == "ambiguous" and not comparison_candidates:
            comparison_candidates = _default_ambiguous_comparison_candidates(metric_keys)
        default_comparison_basis = _validated_optional_scalar(
            candidate.get("default_comparison_basis"),
            allowed=VALID_COMPARISON_BASES - {"none", "ambiguous"},
        )
        if default_comparison_basis is None:
            default_comparison_basis = _default_comparison_basis(
                comparison_basis,
                comparison_candidates,
            )

        period_kind = _validated_scalar(
            candidate.get("period_kind"),
            allowed=VALID_PERIOD_KINDS,
            fallback="unspecified",
        )
        target_period = _validated_scalar(
            candidate.get("target_period"),
            allowed=VALID_TARGET_PERIODS,
            fallback="unspecified",
        )
        duration_class = _validated_optional_scalar(
            candidate.get("duration_class"),
            allowed=VALID_DURATION_CLASSES,
        )
        duration_class = _infer_duration_class(
            query.original,
            period_kind=period_kind,
            duration_class=duration_class,
            comparison_basis=default_comparison_basis or comparison_basis,
        )
        if target_period == "unspecified" and time_scope == "latest":
            target_period = "latest"
        if duration_class is None and target_period == "latest":
            duration_class = _default_duration_class_for_latest_metrics(metric_keys)
        if period_kind == "unspecified" and duration_class in VALID_PERIOD_KINDS:
            period_kind = duration_class

        candidate_forms = _validated_forms(candidate.get("forms"))
        explicit_form_in_question = _question_mentions_form_type(query.original)
        forms = candidate_forms if explicit_form_in_question else []
        if query.form_type:
            forms = [query.form_type] if query.form_type in VALID_FORMS else forms

        preferred_forms = _validated_forms(candidate.get("preferred_forms"))
        allowed_forms = _validated_forms(candidate.get("allowed_forms"))
        preferred_form = _preferred_form_for_comparison_basis(
            default_comparison_basis or comparison_basis
        )
        for form in candidate_forms:
            if form not in preferred_forms:
                preferred_forms.append(form)
        if preferred_form and not query.form_type and preferred_form not in preferred_forms:
            preferred_forms.append(preferred_form)
        if query.form_type and query.form_type in VALID_FORMS:
            preferred_forms = [query.form_type]
            allowed_forms = [query.form_type]
        elif forms:
            allowed_forms = forms
        else:
            allowed_forms = _default_allowed_forms(
                duration_class=duration_class,
                comparison_basis=default_comparison_basis or comparison_basis,
                preferred_forms=preferred_forms,
            )

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
        if metric_keys and not evidence_roles:
            evidence_roles.append("primary_financial_statement_chunks")
        needs_text_chunks = (
            bool(evidence_roles)
            or question_type not in {"metric"}
            or bool(metric_keys)
        )
        compiled_queries = self._compiler.compile(
            query,
            question_type=question_type,
            target_sections=target_sections,
            metric_keys=metric_keys,
            time_scope=time_scope,
            comparison_basis=comparison_basis,
            needs_metric_comparisons=needs_metric_comparisons,
            duration_class=duration_class,
        )

        return RetrievalPlan(
            question_type=question_type,
            target_sections=target_sections,
            metric_keys=metric_keys,
            time_scope=time_scope,
            period_kind=None if period_kind == "unspecified" else period_kind,
            target_period=None if target_period == "unspecified" else target_period,
            duration_class=duration_class,
            comparison_basis=comparison_basis,
            comparison_candidates=comparison_candidates,
            default_comparison_basis=default_comparison_basis,
            ambiguities=_as_nonempty_str_list(candidate.get("ambiguities")),
            forms=forms,
            allowed_forms=allowed_forms,
            preferred_forms=preferred_forms,
            dense_queries=compiled_queries.dense_queries,
            dense_query_specs=compiled_queries.dense_query_specs,
            lexical_queries=compiled_queries.lexical_queries,
            lexical_query_specs=compiled_queries.lexical_query_specs,
            matched_rules=["planner:llm", "validation:schema"],
            planner_source=planner_source,
            needs_financial_facts=needs_financial_facts,
            needs_text_chunks=needs_text_chunks,
            needs_metric_comparisons=needs_metric_comparisons,
            evidence_roles=evidence_roles,
        )


class QueryPlanner:
    def __init__(
        self,
        *,
        settings: Settings | None = None,
        normalizer: QueryNormalizer | None = None,
        validator: PlanValidator | None = None,
        llm_planner: LLMPlanner | None = None,
        dense_query_rewriter: DenseQueryRewriter | None = None,
    ) -> None:
        self._settings = settings or get_settings()
        self._normalizer = normalizer or QueryNormalizer()
        self._validator = validator or PlanValidator()
        self._llm_planner = llm_planner
        self._llm_planner_was_injected = llm_planner is not None
        self._dense_query_rewriter = dense_query_rewriter

    def plan(
        self,
        question: str,
        *,
        form_type: str | None = None,
        section: str | None = None,
    ) -> RetrievalPlan:
        query = self._normalizer.normalize(question, form_type=form_type, section=section)
        if self._settings.query_planner_mode == "rule_only":
            return self._safe_text_plan(query, reason="legacy_rule_only_mode")

        try:
            candidate = self._get_llm_planner().plan_candidate(query.original)
            plan = self._validator.validate(candidate, query, planner_source="llm_validated")
            return self._plan_with_llm_dense_queries(
                query,
                plan,
                inline_specs=candidate.get("dense_query_specs"),
            )
        except Exception as exc:
            return self._safe_text_plan(query, reason=f"llm_failed:{exc.__class__.__name__}")

    def _safe_text_plan(self, query: NormalizedQuery, *, reason: str) -> RetrievalPlan:
        forms = [query.form_type] if query.form_type in VALID_FORMS else []
        query_section = _validated_query_section(query.section)
        target_sections = [query_section] if query_section else []
        return RetrievalPlan(
            question_type="prose",
            target_sections=target_sections,
            metric_keys=[],
            time_scope="unspecified",
            period_kind=None,
            target_period=None,
            duration_class=None,
            comparison_basis="none",
            comparison_candidates=[],
            default_comparison_basis=None,
            ambiguities=[
                "LLM query planning was unavailable; using broad text retrieval only."
            ],
            forms=forms,
            allowed_forms=forms,
            preferred_forms=forms,
            dense_queries=[query.original],
            dense_query_specs=[
                {"role": "original", "text": query.original, "weight": 1.0}
            ],
            lexical_queries=[query.original],
            lexical_query_specs=[
                {"role": "original", "queries": [query.original], "weight": 0.45}
            ],
            matched_rules=["planner:safe_text_fallback", reason],
            planner_source="fallback_validated",
            needs_financial_facts=False,
            needs_text_chunks=True,
            needs_metric_comparisons=False,
            evidence_roles=_evidence_roles_for("prose", target_sections, False),
            requires_llm_fallback_reason=reason,
        )

    def _get_llm_planner(self) -> LLMPlanner:
        if self._llm_planner is None:
            self._llm_planner = LLMQueryPlanner(self._settings)
        return self._llm_planner

    def _get_dense_query_rewriter(self) -> DenseQueryRewriter | None:
        if self._dense_query_rewriter is not None:
            return self._dense_query_rewriter
        if self._llm_planner_was_injected:
            return None
        self._dense_query_rewriter = LLMDenseQueryRewriter(self._settings)
        return self._dense_query_rewriter

    def _plan_with_llm_dense_queries(
        self,
        query: NormalizedQuery,
        plan: RetrievalPlan,
        *,
        inline_specs: Any | None = None,
    ) -> RetrievalPlan:
        requested_roles = _requested_dense_roles_for(
            question_type=plan.question_type,
            target_sections=plan.target_sections,
            metric_keys=plan.metric_keys,
        )

        # Single-call fast path: the planner LLM already proposed dense query
        # specs alongside the validated slots, so a second rewriter round trip
        # is only needed when those inline specs fail validation.
        if inline_specs is not None:
            inline_validated = _validated_llm_dense_query_specs(
                inline_specs,
                requested_roles=requested_roles,
                comparison_basis=plan.comparison_basis,
                duration_class=plan.duration_class,
            )
            if inline_validated:
                merged = _merge_dense_query_specs(
                    llm_specs=inline_validated,
                    fallback_specs=plan.dense_query_specs,
                    requested_roles=requested_roles,
                    original_query=query.original,
                )
                if merged:
                    return replace(
                        plan,
                        dense_query_specs=merged,
                        dense_queries=[spec["text"] for spec in merged],
                        matched_rules=[
                            *plan.matched_rules,
                            "dense_query:planner_single_call",
                        ],
                    )

        rewriter = self._get_dense_query_rewriter()
        if rewriter is None:
            return plan
        try:
            raw_specs = rewriter.rewrite(
                question=query.original,
                plan=plan,
                requested_roles=requested_roles,
            )
            llm_specs = _validated_llm_dense_query_specs(
                raw_specs,
                requested_roles=requested_roles,
                comparison_basis=plan.comparison_basis,
                duration_class=plan.duration_class,
            )
        except Exception as exc:
            return replace(
                plan,
                matched_rules=[
                    *plan.matched_rules,
                    f"dense_query:hardcoded_fallback:{exc.__class__.__name__}",
                ],
            )

        if not llm_specs:
            return replace(
                plan,
                matched_rules=[
                    *plan.matched_rules,
                    "dense_query:hardcoded_fallback:no_valid_specs",
                ],
            )

        dense_query_specs = _merge_dense_query_specs(
            llm_specs=llm_specs,
            fallback_specs=plan.dense_query_specs,
            requested_roles=requested_roles,
            original_query=query.original,
        )
        if not dense_query_specs:
            return replace(
                plan,
                matched_rules=[
                    *plan.matched_rules,
                    "dense_query:hardcoded_fallback:no_valid_specs",
                ],
            )
        return replace(
            plan,
            dense_query_specs=dense_query_specs,
            dense_queries=[spec["text"] for spec in dense_query_specs],
            matched_rules=[*plan.matched_rules, "dense_query:llm_rewriter"],
        )


def _llm_planner_system_prompt() -> str:
    return f"""
You are the query planner for an SEC-filing equity research copilot.

Your job is to read the user's natural-language question and produce one strict JSON
object of semantic planning slots. Do not answer the research question. Do not cite
facts. Do not generate retrieval queries. A separate dense query rewriter will use
validated slots to generate dense embedding retrieval queries.

Prefer semantic understanding over keyword matching. The question is expected to be
English.

Allowed JSON fields:
{_format_allowed_values(VALID_LLM_PLAN_FIELDS)}

Allowed question_type values:
{_format_allowed_values(VALID_QUESTION_TYPES)}

Allowed metric_keys values:
{_format_allowed_values(METRIC_RETRIEVAL_PROFILES.keys())}

Allowed time_scope values:
{_format_allowed_values(VALID_TIME_SCOPES)}

Allowed period_kind values:
{_format_allowed_values(VALID_PERIOD_KINDS)}

Allowed target_period values:
{_format_allowed_values(VALID_TARGET_PERIODS)}

Allowed duration_class values:
{_format_allowed_values(VALID_DURATION_CLASSES)}

Allowed comparison_basis values:
{_format_allowed_values(VALID_COMPARISON_BASES)}

Allowed target_sections values:
{_format_allowed_values(VALID_TARGET_SECTIONS)}

Allowed forms values:
{_format_allowed_values(VALID_FORMS)}

Field guidance:
- metric_keys should contain only normalized XBRL metrics that directly help answer the question.
- For broad company performance questions, such as "How did Apple do last quarter?", use metric_keys=["revenue","gross_margin","operating_income","net_income"].
- target_sections should name filing sections worth retrieving as text evidence.
- forms should constrain retrieval only when the user explicitly asks for a specific form such as latest 10-Q, latest 10-K, or 8-K.
- allowed_forms can include non-explicit form fallbacks, e.g. last-quarter metric questions can prefer 10-Q but allow 10-K for Q4.
- For "last/latest quarter" metric questions, set period_kind="quarter", target_period="latest", duration_class="quarter".
- For balance sheet point-in-time metric questions, set period_kind="instant", target_period="latest", duration_class="instant".
- comparison_basis is "none" for point-in-time or summary questions, "ambiguous" when the user asks for change/growth without specifying quarterly, YTD, or fiscal-year basis, and a specific basis when implied or stated.
- Do not output dense_queries, lexical_queries, lexical_query_specs, evidence_roles, needs_financial_facts, needs_text_chunks, or needs_metric_comparisons.

Dense query specs:
Also return dense_query_specs: short English evidence-seeking phrases used for dense embedding retrieval. They are not answers; they describe the filing language likely to contain evidence.
- Include one spec per applicable role, chosen from: slot, financial_statement, cash_flow, liquidity, mda, risk.
- Always include role "slot": one broad semantic query covering the overall evidence need across metrics, time scope, and comparison basis.
- Include "financial_statement" when metric_keys is non-empty or target_sections includes Financial Statements or Cash Flows; focus on statement names and line items.
- Include "cash_flow" when target_sections includes Cash Flows or metric_keys includes operating_cash_flow, free_cash_flow, or capital_expenditures; focus on statements of cash flows and operating/investing/financing activities.
- Include "liquidity" when target_sections includes Liquidity or metric_keys includes operating_cash_flow or free_cash_flow; focus on liquidity and capital resources.
- Include "mda" when target_sections includes Management's Discussion and Analysis or question_type is mixed, performance_overview, or performance_judgment; focus on results of operations, drivers, "primarily due to".
- Include "risk" when target_sections includes Risk Factors or question_type is risk; focus on risk factors item 1A language.
- Each text must be 8 to 32 words, English only, neutral SEC filing evidence language.
- Do not invent numbers, percentages, dates, dollar amounts, causes, or outcomes.
- If comparison_basis is not "none", include matching period or comparison language such as "three months ended" or "year over year".

Few-shot examples:

Input:
How did Apple do last quarter?
Output:
{{
  "question_type": "performance_overview",
  "target_sections": ["Financial Statements", "Management's Discussion and Analysis"],
  "metric_keys": ["revenue", "gross_margin", "operating_income", "net_income"],
  "time_scope": "latest",
  "comparison_basis": "latest_quarter_yoy",
  "comparison_candidates": ["latest_quarter_yoy"],
  "default_comparison_basis": "latest_quarter_yoy",
  "period_kind": "quarter",
  "target_period": "latest",
  "duration_class": "quarter",
  "ambiguities": [],
  "forms": [],
  "allowed_forms": ["10-Q", "10-K"],
  "preferred_forms": ["10-Q"],
  "dense_query_specs": [
    {{"role": "slot", "text": "latest quarterly financial performance showing year over year revenue margin and income changes"}},
    {{"role": "financial_statement", "text": "consolidated statements of operations showing three months ended net sales gross margin operating income and net income"}},
    {{"role": "mda", "text": "results of operations discussion explaining quarterly performance drivers compared to the prior year quarter"}}
  ]
}}

Input:
How did Apple's revenue and gross margin change last quarter, and why?
Output:
{{
  "question_type": "mixed",
  "target_sections": ["Financial Statements", "Management's Discussion and Analysis"],
  "metric_keys": ["revenue", "gross_margin"],
  "time_scope": "latest",
  "comparison_basis": "latest_quarter_yoy",
  "comparison_candidates": ["latest_quarter_yoy"],
  "default_comparison_basis": "latest_quarter_yoy",
  "period_kind": "quarter",
  "target_period": "latest",
  "duration_class": "quarter",
  "ambiguities": [],
  "forms": [],
  "allowed_forms": ["10-Q", "10-K"],
  "preferred_forms": ["10-Q"]
}}

Input:
Summarize Apple's latest 10-K risk factors.
Output:
{{
  "question_type": "risk",
  "target_sections": ["Risk Factors"],
  "metric_keys": [],
  "time_scope": "latest",
  "comparison_basis": "none",
  "comparison_candidates": [],
  "default_comparison_basis": null,
  "period_kind": "fy",
  "target_period": "latest",
  "duration_class": "fy",
  "ambiguities": [],
  "forms": ["10-K"],
  "allowed_forms": ["10-K"],
  "preferred_forms": ["10-K"],
  "dense_query_specs": [
    {{"role": "slot", "text": "latest annual report evidence summarizing business operational financial and regulatory risks"}},
    {{"role": "risk", "text": "risk factors item 1a discussion of business legal regulatory market operational and financial risks"}}
  ]
}}

Input:
How much cash did Apple generate?
Output:
{{
  "question_type": "metric",
  "target_sections": ["Cash Flows", "Liquidity"],
  "metric_keys": ["operating_cash_flow"],
  "time_scope": "latest",
  "comparison_basis": "none",
  "comparison_candidates": [],
  "default_comparison_basis": null,
  "period_kind": "quarter",
  "target_period": "latest",
  "duration_class": "quarter",
  "ambiguities": [],
  "forms": [],
  "allowed_forms": ["10-Q", "10-K"],
  "preferred_forms": ["10-Q"]
}}

Return only valid JSON. Use empty lists when no allowed value applies.
""".strip()


def _llm_dense_query_rewriter_system_prompt() -> str:
    return """
You generate dense embedding retrieval queries for SEC filing search.

Your job is not to answer the user's question. Your job is to write short English
evidence-seeking phrases that help retrieve relevant SEC filing passages.

Return only JSON:
{"dense_query_specs":[{"role":"...", "text":"..."}]}

Rules:
- Generate exactly one query for each requested role, and no other roles.
- Each text must be 8 to 32 words.
- Use English only.
- Use neutral SEC filing evidence language, not conclusions.
- Do not invent numbers, percentages, dates, dollar amounts, causes, or outcomes.
- Do not mention metrics outside metric_keys, except supporting terms listed in allowed_metric_terms.
- Do not mention sections outside target_sections, except generic filing anchors needed for the requested role.
- Omit the company name unless it is needed to distinguish a product, segment, or peer.
- If comparison_basis is not "none", include matching period/comparison language.
- For slot, write one broad semantic query that captures the overall evidence need across the requested metrics, time scope, and comparison basis.
- For financial_statement, focus on statement names, line items, and reported amounts.
- For mda, focus on results of operations, management discussion, drivers, reasons, "primarily due to", "higher", "lower".
- For cash_flow, focus on statements of cash flows and operating/investing/financing activities.
- For liquidity, focus on liquidity and capital resources, cash requirements, and funding capacity.
- For risk, focus on risk factors, Item 1A, business, operational, financial, legal, or regulatory risks.

Few-shot examples:

Input:
{
  "original_question": "How did Apple's revenue and gross margin change last quarter, and why?",
  "validated_slots": {
    "question_type": "mixed",
    "metric_keys": ["revenue", "gross_margin"],
    "target_sections": ["Financial Statements", "Management's Discussion and Analysis"],
    "time_scope": "latest",
    "comparison_basis": "latest_quarter_yoy"
  },
  "requested_roles": ["slot", "financial_statement", "mda"],
  "allowed_metric_terms": {
    "revenue": ["revenue", "net sales", "total net sales"],
    "gross_margin": ["gross margin", "gross profit", "cost of sales"]
  },
  "allowed_comparison_terms": ["latest quarter", "three months ended", "year over year", "compared to prior year"]
}
Output:
{
  "dense_query_specs": [
    {
      "role": "slot",
      "text": "latest quarterly financial performance showing year over year revenue and gross margin changes"
    },
    {
      "role": "financial_statement",
      "text": "consolidated statements of operations showing three months ended net sales gross margin and cost of sales"
    },
    {
      "role": "mda",
      "text": "results of operations discussion explaining revenue and gross margin drivers compared to the prior year quarter"
    }
  ]
}

Input:
{
  "original_question": "How much cash did Apple generate from operations, and what does it say about liquidity?",
  "validated_slots": {
    "question_type": "liquidity",
    "metric_keys": ["operating_cash_flow"],
    "target_sections": ["Cash Flows", "Liquidity"],
    "time_scope": "latest",
    "comparison_basis": "none"
  },
  "requested_roles": ["slot", "financial_statement", "cash_flow", "liquidity"],
  "allowed_metric_terms": {
    "operating_cash_flow": ["operating cash flow", "net cash provided by operating activities", "operating activities"]
  },
  "allowed_comparison_terms": []
}
Output:
{
  "dense_query_specs": [
    {"role": "slot", "text": "latest operating cash flow evidence showing cash generation and liquidity position"},
    {"role": "financial_statement", "text": "statements of cash flows showing net cash provided by operating activities reported amount"},
    {"role": "cash_flow", "text": "statements of cash flows discussion of operating investing and financing activities cash generation"},
    {"role": "liquidity", "text": "liquidity and capital resources discussion of cash requirements working capital and funding capacity"}
  ]
}

Input:
{
  "original_question": "Summarize Apple's latest 10-K risk factors.",
  "validated_slots": {
    "question_type": "risk",
    "metric_keys": [],
    "target_sections": ["Risk Factors"],
    "time_scope": "latest",
    "comparison_basis": "none"
  },
  "requested_roles": ["slot", "risk"],
  "allowed_metric_terms": {},
  "allowed_comparison_terms": []
}
Output:
{
  "dense_query_specs": [
    {"role": "slot", "text": "latest annual report evidence summarizing business operational financial and regulatory risks"},
    {"role": "risk", "text": "risk factors item 1a discussion of business legal regulatory market operational and financial risks"}
  ]
}

Return only valid JSON.
""".strip()


def _parse_llm_json(content: str) -> dict[str, Any]:
    stripped = content.strip()
    if stripped.startswith("```"):
        stripped = re.sub(r"^```(?:json)?\s*", "", stripped)
        stripped = re.sub(r"\s*```$", "", stripped)
    parsed = json.loads(stripped)
    if not isinstance(parsed, dict):
        raise ValueError("LLM planner response must be a JSON object.")
    return parsed


def _format_allowed_values(values: Any) -> str:
    return "\n".join(f"- {value}" for value in sorted(values))


def _llm_dense_query_rewriter_payload(
    *,
    question: str,
    plan: RetrievalPlan,
    requested_roles: list[str],
) -> dict[str, Any]:
    return {
        "original_question": question,
        "validated_slots": {
            "question_type": plan.question_type,
            "metric_keys": plan.metric_keys,
            "target_sections": plan.target_sections,
            "time_scope": plan.time_scope,
            "period_kind": plan.period_kind,
            "target_period": plan.target_period,
            "duration_class": plan.duration_class,
            "comparison_basis": plan.comparison_basis,
        },
        "requested_roles": requested_roles,
        "allowed_metric_terms": _dense_rewriter_metric_terms(plan.metric_keys),
        "allowed_section_terms": {
            section: DENSE_REWRITER_SECTION_TERMS[section]
            for section in plan.target_sections
            if section in DENSE_REWRITER_SECTION_TERMS
        },
        "allowed_comparison_terms": DENSE_REWRITER_COMPARISON_TERMS.get(
            plan.comparison_basis,
            [],
        ),
        "allowed_period_terms": _period_terms_for_duration(plan.duration_class),
    }


def _period_terms_for_duration(duration_class: str | None) -> list[str]:
    if duration_class == "quarter":
        return ["latest quarter", "three months ended", "quarterly"]
    if duration_class == "ytd":
        return ["year to date", "six months ended", "nine months ended"]
    if duration_class == "fy":
        return ["fiscal year", "year ended", "annual"]
    if duration_class == "instant":
        return ["period end", "as of", "balance sheet"]
    return []


def _requested_dense_roles_for(
    *,
    question_type: str,
    target_sections: list[str],
    metric_keys: list[str],
) -> list[str]:
    roles: list[str] = ["slot"]
    if metric_keys or any(
        section in target_sections for section in {"Financial Statements", "Cash Flows"}
    ):
        roles.append("financial_statement")
    if "Cash Flows" in target_sections or any(
        metric in metric_keys
        for metric in {"operating_cash_flow", "free_cash_flow", "capital_expenditures"}
    ):
        roles.append("cash_flow")
    if "Liquidity" in target_sections or any(
        metric in metric_keys for metric in {"operating_cash_flow", "free_cash_flow"}
    ):
        roles.append("liquidity")
    if (
        "Management's Discussion and Analysis" in target_sections
        or question_type in {"mixed", "performance_overview", "performance_judgment"}
    ):
        roles.append("mda")
    if "Risk Factors" in target_sections or question_type == "risk":
        roles.append("risk")
    return [
        role
        for role in _dedupe(roles)
        if role in VALID_LLM_DENSE_QUERY_ROLES
    ][:MAX_LLM_DENSE_QUERY_ROLES]


def _dense_rewriter_metric_terms(metric_keys: list[str]) -> dict[str, list[str]]:
    terms: dict[str, list[str]] = {}
    for metric_key in metric_keys:
        profile = get_metric_profile(metric_key)
        if profile is None:
            terms[metric_key] = [metric_key.replace("_", " ")]
            continue
        terms[metric_key] = _dedupe(
            [
                metric_key.replace("_", " "),
                *profile.strong_terms[:4],
                *profile.statement_terms[:4],
                *profile.weak_terms[:3],
                *profile.aliases[:5],
            ]
        )[:12]
    return terms


def _validated_llm_dense_query_specs(
    value: Any,
    *,
    requested_roles: list[str],
    comparison_basis: str,
    duration_class: str | None = None,
) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []

    allowed_roles = set(requested_roles).intersection(VALID_LLM_DENSE_QUERY_ROLES)
    specs: list[dict[str, Any]] = []
    seen_roles: set[str] = set()
    for item in value:
        if not isinstance(item, dict):
            continue
        role = _normalize_role(str(item.get("role", "")))
        if role not in allowed_roles or role in seen_roles:
            continue
        text = " ".join(str(item.get("text", "")).split())
        if not _valid_llm_dense_query_text(
            text,
            role=role,
            comparison_basis=comparison_basis,
            duration_class=duration_class,
        ):
            continue
        specs.append(
            {
                "role": role,
                "text": text,
                "weight": DENSE_ROLE_WEIGHTS.get(role, DENSE_WEIGHT_PRIMARY),
            }
        )
        seen_roles.add(role)
    return specs


def _valid_llm_dense_query_text(
    text: str,
    *,
    role: str,
    comparison_basis: str,
    duration_class: str | None = None,
) -> bool:
    words = text.split()
    if not 8 <= len(words) <= 32:
        return False
    if not text.isascii():
        return False
    if _contains_forbidden_dense_fact_pattern(text):
        return False
    if _has_disallowed_comparison_context(
        text,
        comparison_basis,
        duration_class=duration_class,
    ):
        return False
    if not _has_role_anchor(text, role):
        return False
    return True


def _contains_forbidden_dense_fact_pattern(text: str) -> bool:
    allowed_anchor_text = re.sub(r"\bitem\s+1a\b", "", text, flags=re.IGNORECASE)
    allowed_anchor_text = re.sub(
        r"\b(?:10-k|10-q|8-k)\b",
        "",
        allowed_anchor_text,
        flags=re.IGNORECASE,
    )
    return (
        re.search(r"[$€£¥%]", allowed_anchor_text) is not None
        or re.search(r"\b\d+(?:\.\d+)?\b", allowed_anchor_text) is not None
        or re.search(
            r"\b(?:million|billion|trillion|dollars?)\b",
            allowed_anchor_text,
            flags=re.IGNORECASE,
        )
        is not None
    )


def _has_disallowed_comparison_context(
    text: str,
    comparison_basis: str,
    *,
    duration_class: str | None = None,
) -> bool:
    normalized = text.lower()
    if comparison_basis == "ambiguous":
        return False
    quarterly_terms = ("three months ended", "quarter", "quarterly")
    ytd_terms = (
        "six months ended",
        "nine months ended",
        "year to date",
        "year-to-date",
        "ytd",
    )
    fiscal_year_terms = ("fiscal year", "year ended", "annual report", "full year")

    if comparison_basis in {"latest_quarter_yoy", "previous_quarter_yoy"}:
        disallowed_groups = (ytd_terms, fiscal_year_terms)
    elif comparison_basis in {"latest_ytd_yoy", "previous_ytd_yoy"}:
        disallowed_groups = (quarterly_terms, fiscal_year_terms)
    elif comparison_basis in {"latest_fy_yoy", "previous_fy_yoy"}:
        disallowed_groups = (quarterly_terms, ytd_terms)
    elif comparison_basis == "none" and duration_class == "quarter":
        disallowed_groups = (ytd_terms, fiscal_year_terms)
    elif comparison_basis == "none" and duration_class == "ytd":
        disallowed_groups = (quarterly_terms, fiscal_year_terms)
    elif comparison_basis == "none" and duration_class == "fy":
        disallowed_groups = (quarterly_terms, ytd_terms)
    else:
        disallowed_groups = ()
    return any(term in normalized for group in disallowed_groups for term in group)


def _has_role_anchor(text: str, role: str) -> bool:
    if role == "slot":
        return True
    normalized = text.lower()
    role_anchors = {
        "financial_statement": (
            "statement",
            "statements",
            "line item",
            "reported amount",
            "operations",
            "cash flows",
            "balance sheet",
        ),
        "cash_flow": ("cash flow", "cash flows", "operating activities", "investing", "financing"),
        "liquidity": ("liquidity", "capital resources", "cash requirements", "funding", "working capital"),
        "mda": (
            "management discussion",
            "results of operations",
            "driver",
            "drivers",
            "reason",
            "reasons",
            "primarily due",
            "discussion",
            "explaining",
        ),
        "risk": ("risk", "risks", "risk factors", "item 1a"),
    }
    return any(anchor in normalized for anchor in role_anchors.get(role, ()))


def _merge_dense_query_specs(
    *,
    llm_specs: list[dict[str, Any]],
    fallback_specs: list[dict[str, Any]],
    requested_roles: list[str],
    original_query: str,
) -> list[dict[str, Any]]:
    specs: list[dict[str, Any]] = []
    llm_by_role = {spec["role"]: spec for spec in llm_specs}
    fallback_by_role = {
        spec["role"]: spec
        for spec in fallback_specs
        if spec.get("role") in {*requested_roles, "original"}
    }
    for role in requested_roles[:MAX_LLM_DENSE_QUERY_ROLES]:
        spec = llm_by_role.get(role) or fallback_by_role.get(role)
        if spec is not None:
            _append_dense_query_spec(specs, spec)
    original_spec = fallback_by_role.get(
        "original",
        {
            "role": "original",
            "text": original_query,
            "weight": DENSE_WEIGHT_ORIGINAL_QUERY,
        },
    )
    _append_dense_query_spec(specs, original_spec)
    return specs


def _append_dense_query_spec(
    specs: list[dict[str, Any]],
    spec: dict[str, Any],
) -> None:
    role = _normalize_role(str(spec.get("role", "")))
    text = " ".join(str(spec.get("text", "")).split())
    if not role or not text or any(existing["text"] == text for existing in specs):
        return
    specs.append(
        {
            "role": role,
            "text": text,
            "weight": _coerce_weight(
                spec.get("weight", DENSE_ROLE_WEIGHTS.get(role, DENSE_WEIGHT_PRIMARY))
            ),
        }
    )


def _validated_scalar(value: Any, *, allowed: set[str], fallback: str) -> str:
    if isinstance(value, str) and value in allowed:
        return value
    return fallback


def _validated_optional_scalar(value: Any, *, allowed: set[str]) -> str | None:
    if isinstance(value, str) and value in allowed:
        return value
    return None


def _validated_list(value: Any, *, allowed: set[str]) -> list[str]:
    return [item for item in _as_nonempty_str_list(value) if item in allowed]


def _validated_forms(value: Any) -> list[str]:
    return [
        form
        for form in _dedupe(item.upper() for item in _as_nonempty_str_list(value))
        if form in VALID_FORMS
    ]


def _validated_query_section(value: str | None) -> str | None:
    if isinstance(value, str) and value in VALID_TARGET_SECTIONS:
        return value
    return None


def _as_nonempty_str_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        raw_items = [value]
    elif isinstance(value, list):
        raw_items = value
    else:
        return []
    return _dedupe(
        " ".join(str(item).split())
        for item in raw_items
        if str(item).strip()
    )


def _compile_lexical_query_specs(
    *,
    metric_keys: list[str],
    target_sections: list[str],
    comparison_basis: str,
    question_type: str,
) -> list[dict[str, Any]]:
    statement_groups: list[list[str]] = []
    mda_groups: list[list[str]] = []
    supporting_groups: list[list[str]] = []

    for section in target_sections:
        terms = list(LEXICAL_SECTION_TERMS.get(section, ()))
        if not terms:
            continue
        if section in {"Financial Statements", "Cash Flows"}:
            statement_groups.append(terms)
        elif section in {"Management's Discussion and Analysis", "Liquidity"}:
            mda_groups.append(terms)
        else:
            supporting_groups.append(terms)

    metric_statement_groups: list[list[str]] = []
    metric_mda_groups: list[list[str]] = []
    for metric_key in metric_keys:
        statement_queries, mda_queries = _metric_lexical_query_groups(
            metric_key,
            comparison_basis,
        )
        if statement_queries:
            metric_statement_groups.append(statement_queries)
        if mda_queries:
            metric_mda_groups.append(mda_queries)

    comparison_terms = list(LEXICAL_COMPARISON_TERMS.get(comparison_basis, ()))
    statement_queries: list[str] = []
    statement_queries.extend(group[0] for group in statement_groups if group)
    statement_queries.extend(comparison_terms)
    statement_queries.extend(group[0] for group in metric_statement_groups if group)
    statement_queries.extend(_round_robin_query_groups([group[1:] for group in statement_groups]))
    statement_queries.extend(
        _round_robin_query_groups([group[1:] for group in metric_statement_groups])
    )

    mda_weight = 0.75 if question_type in {
        "mixed",
        "management_discussion",
        "trend",
        "growth_acceleration",
        "broad_comparison",
        "performance_overview",
        "performance_judgment",
    } else 0.35
    mda_queries: list[str] = []
    mda_queries.extend(group[0] for group in mda_groups if group)
    mda_queries.extend(group[0] for group in metric_mda_groups if group)
    mda_queries.extend(_round_robin_query_groups([group[1:] for group in mda_groups]))
    mda_queries.extend(_round_robin_query_groups([group[1:] for group in metric_mda_groups]))

    supporting_queries = _round_robin_query_groups(supporting_groups)

    specs: list[dict[str, Any]] = []
    _add_lexical_spec(
        specs,
        role="primary_financial_statement",
        queries=statement_queries,
        weight=1.0,
    )
    _add_lexical_spec(
        specs,
        role="mda_explanation",
        queries=mda_queries,
        weight=mda_weight,
    )
    _add_lexical_spec(
        specs,
        role="supporting_text",
        queries=supporting_queries,
        weight=0.7,
    )
    return specs


def _flatten_lexical_query_specs(specs: list[dict[str, Any]]) -> list[str]:
    queries: list[str] = []
    max_group_length = max(
        (len(spec.get("queries", [])) for spec in specs),
        default=0,
    )
    for index in range(max_group_length):
        for spec in specs:
            spec_queries = spec.get("queries", [])
            if isinstance(spec_queries, list) and index < len(spec_queries):
                queries.append(str(spec_queries[index]))
    return _dedupe(query for query in queries if query.strip())[:MAX_LEXICAL_QUERIES]


def _add_lexical_spec(
    specs: list[dict[str, Any]],
    *,
    role: str,
    queries: list[str],
    weight: float,
) -> None:
    deduped_queries = _dedupe(query for query in queries if query.strip())
    if not deduped_queries:
        return
    specs.append(
        {
            "role": _normalize_role(role),
            "queries": deduped_queries[:MAX_LEXICAL_QUERIES],
            "weight": _coerce_weight(weight),
        }
    )


def _metric_lexical_query_groups(
    metric_key: str,
    comparison_basis: str,
) -> tuple[list[str], list[str]]:
    profile = get_metric_profile(metric_key)
    if profile is None:
        return [], []
    period_queries = [
        query
        for query in profile.lexical_queries
        if _matches_lexical_comparison_basis(query, comparison_basis)
    ]
    metric_queries = _dedupe(
        [
        *period_queries,
        *profile.lexical_queries[:6],
        *(f'"{term}"' for term in profile.strong_terms[:3]),
        *(f'"{term}"' for term in profile.statement_terms[:3]),
        ]
    )
    statement_queries = [
        query for query in metric_queries if not _is_mda_lexical_query(query)
    ]
    mda_queries = [
        query for query in metric_queries if _is_mda_lexical_query(query)
    ]
    return statement_queries, mda_queries


def _is_mda_lexical_query(query: str) -> bool:
    normalized = query.lower()
    return any(
        marker in normalized
        for marker in (
            "growth",
            "increased",
            "decreased",
            "primarily due",
            "compared to",
            "compared with",
        )
    )


def _matches_lexical_comparison_basis(query: str, comparison_basis: str) -> bool:
    normalized = query.lower()
    if comparison_basis in {
        "latest_quarter_yoy",
        "previous_quarter_yoy",
        "duration_quarter",
    }:
        markers = ("three months ended",)
    elif comparison_basis in {
        "latest_ytd_yoy",
        "previous_ytd_yoy",
        "duration_ytd",
    }:
        markers = (
            "six months ended",
            "nine months ended",
            "year to date",
            "year-to-date",
        )
    elif comparison_basis in {
        "latest_fy_yoy",
        "previous_fy_yoy",
        "duration_fy",
    }:
        markers = ("year ended", "fiscal year")
    elif comparison_basis == "duration_instant":
        markers = ("as of", "period end", "balance sheet")
    else:
        markers = ()
    return any(marker in normalized for marker in markers)


def _round_robin_query_groups(groups: list[list[str]]) -> list[str]:
    queries: list[str] = []
    max_group_length = max((len(group) for group in groups), default=0)
    for index in range(max_group_length):
        for group in groups:
            if index < len(group):
                queries.append(group[index])
    return queries


def _default_metric_keys_for_question_type(question_type: str) -> list[str]:
    if question_type in {"performance_overview", "performance_judgment", "broad_comparison"}:
        return list(PERFORMANCE_OVERVIEW_METRICS)
    if question_type == "filing_summary":
        return list(SUMMARY_METRICS)
    if question_type == "liquidity":
        return list(LIQUIDITY_METRICS)
    if question_type == "growth_acceleration":
        return list(GROWTH_METRICS)
    return []


def _metric_dense_phrases(metric_keys: list[str]) -> list[str]:
    phrases: list[str] = []
    for metric_key in metric_keys:
        profile = get_metric_profile(metric_key)
        if profile is None:
            phrases.append(metric_key.replace("_", " "))
            continue
        phrases.extend(profile.strong_terms[:3])
        phrases.extend(profile.statement_terms[:3])
        phrases.extend(profile.weak_terms[:2])
    return _dedupe(phrases)


def _section_dense_phrases(target_sections: list[str]) -> list[str]:
    return [
        SECTION_DENSE_CONTEXT[section]
        for section in target_sections
        if section in SECTION_DENSE_CONTEXT
    ]


def _time_dense_context(time_scope: str) -> str:
    if time_scope == "latest":
        return "latest most recent filing period"
    if time_scope == "comparison_trend":
        return "trend growth change compared with prior period"
    return ""


def _join_terms(terms: list[str], *, max_words: int) -> str:
    selected: list[str] = []
    words_used = 0
    for term in _dedupe(" ".join(term.split()) for term in terms if term.strip()):
        word_count = len(term.split())
        if selected and words_used + word_count > max_words:
            continue
        selected.append(term)
        words_used += word_count
    return " ".join(selected)


def _coerce_weight(value: Any) -> float:
    try:
        weight = float(value)
    except (TypeError, ValueError):
        return 1.0
    return min(max(weight, 0.05), 2.0)


def _comparison_requested(
    comparison_basis: str,
    comparison_candidates: list[str],
) -> bool:
    return bool(
        comparison_candidates
        or comparison_basis not in {"none", "ambiguous"}
    )


def _default_comparison_basis(
    comparison_basis: str,
    comparison_candidates: list[str],
) -> str | None:
    if comparison_candidates:
        return comparison_candidates[0]
    if comparison_basis not in {"none", "ambiguous"}:
        return comparison_basis
    return None


def _default_ambiguous_comparison_candidates(metric_keys: list[str]) -> list[str]:
    default_basis = _default_comparison_basis_for_metrics(metric_keys)
    candidates = [
        default_basis,
        "latest_ytd_yoy",
        "latest_fy_yoy",
    ]
    return _dedupe(candidates)


def _default_comparison_basis_for_metrics(metric_keys: list[str]) -> str:
    for metric_key in metric_keys:
        profile = get_metric_profile(metric_key)
        if profile is not None and profile.default_comparison_basis:
            return profile.default_comparison_basis
    return "latest_quarter_yoy"


def _lexical_basis_for_duration(duration_class: str | None) -> str:
    if duration_class == "quarter":
        return "duration_quarter"
    if duration_class == "ytd":
        return "duration_ytd"
    if duration_class == "fy":
        return "duration_fy"
    if duration_class == "instant":
        return "duration_instant"
    return "none"


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


def _default_duration_class_for_latest_metrics(metric_keys: list[str]) -> str | None:
    defaults: list[str] = []
    for metric_key in metric_keys:
        profile = get_metric_profile(metric_key)
        if profile is None:
            continue
        default = profile.default_duration_class_for_latest
        if default in VALID_DURATION_CLASSES:
            defaults.append(default)
    unique_defaults = list(dict.fromkeys(defaults))
    if len(unique_defaults) == 1:
        return unique_defaults[0]
    return None


def _infer_duration_class(
    question: str,
    *,
    period_kind: str,
    duration_class: str | None,
    comparison_basis: str | None,
) -> str | None:
    if duration_class in VALID_DURATION_CLASSES:
        return duration_class
    if period_kind in VALID_DURATION_CLASSES:
        return period_kind
    if comparison_basis in {"latest_quarter_yoy", "previous_quarter_yoy"}:
        return "quarter"
    if comparison_basis in {"latest_ytd_yoy", "previous_ytd_yoy"}:
        return "ytd"
    if comparison_basis in {"latest_fy_yoy", "previous_fy_yoy"}:
        return "fy"

    normalized = question.lower()
    if any(
        marker in normalized
        for marker in (
            "cash on hand",
            "cash balance",
            "cash did apple have",
            "assets did",
            "liabilities did",
            "balance sheet",
            "as of",
            "period end",
        )
    ):
        return "instant"
    if any(
        marker in normalized
        for marker in (
            "last quarter",
            "latest quarter",
            "most recent quarter",
            "this quarter",
            "quarterly",
            "three months ended",
        )
    ):
        return "quarter"
    if any(
        marker in normalized
        for marker in (
            "year to date",
            "year-to-date",
            "ytd",
            "six months ended",
            "nine months ended",
        )
    ):
        return "ytd"
    if any(
        marker in normalized
        for marker in (
            "full year",
            "fiscal year",
            "latest year",
            "annual",
            "year ended",
            "fy ",
        )
    ):
        return "fy"
    return None


def _question_mentions_form_type(question: str) -> bool:
    normalized = question.lower().replace(" ", "")
    return any(form in normalized for form in ("10-k", "10q", "10-q", "8-k", "8k"))


def _default_allowed_forms(
    *,
    duration_class: str | None,
    comparison_basis: str | None,
    preferred_forms: list[str],
) -> list[str]:
    if duration_class == "quarter" or comparison_basis in {
        "latest_quarter_yoy",
        "previous_quarter_yoy",
    }:
        return ["10-Q", "10-K"]
    if duration_class == "instant":
        return ["10-Q", "10-K"]
    if duration_class == "ytd" or comparison_basis in {
        "latest_ytd_yoy",
        "previous_ytd_yoy",
    }:
        return ["10-Q"]
    if duration_class == "fy" or comparison_basis in {
        "latest_fy_yoy",
        "previous_fy_yoy",
    }:
        return ["10-K"]
    return []


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


def _normalize_role(role: str) -> str:
    normalized = re.sub(r"[^a-z0-9_]+", "_", role.strip().lower())
    normalized = re.sub(r"_+", "_", normalized).strip("_")
    return normalized or "query"


def _dedupe(items: Any) -> list[Any]:
    deduped: list[Any] = []
    seen: set[Any] = set()
    for item in items:
        if item in seen:
            continue
        seen.add(item)
        deduped.append(item)
    return deduped

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal
import re
from time import perf_counter
from typing import Any

from sqlalchemy import case, func, or_, select, text
from sqlalchemy.orm import Session

from app.core import Settings, get_settings
from app.models import ChunkEmbedding, Company, DocumentChunk, Filing, FinancialFact
from app.schemas.retrieval import (
    EvidencePackRead,
    EvidenceSpanRead,
    MetricObservationComponentRead,
    MetricObservationRead,
    RetrievedChunkRead,
    RetrievedFinancialFactRead,
    MetricComparisonRead,
    RetrievalRequest,
    RetrievalResponse,
)
from app.services.company_lookup import CompanyLookupError, normalize_ticker
from app.services.embedding_provider import (
    EmbeddingProvider,
    EmbeddingProviderError,
    build_embedding_provider,
)
from app.services.metric_profiles import get_metric_profile
from app.services.query_planner import QueryPlanner, RetrievalPlan

RRF_K = 60
DENSE_WEIGHT = 1.0
LEXICAL_WEIGHT = 0.9
FACT_WEIGHT = 1.1
EVIDENCE_PACK_CHUNK_QUOTAS = {
    "primary_financial_statement_chunks": 2,
    "mda_explanation_chunks": 3,
    "segment_or_product_breakdown_chunks": 2,
    "risk_factor_chunks": 3,
    "annual_context_chunks": 1,
}
EVIDENCE_PACK_SPAN_QUOTAS = {
    "primary_financial_statement_chunks": 2,
    "mda_explanation_chunks": 4,
    "segment_or_product_breakdown_chunks": 3,
    "risk_factor_chunks": 4,
    "annual_context_chunks": 1,
}
EVIDENCE_PACK_CHUNK_ROLE_ORDER = tuple(EVIDENCE_PACK_CHUNK_QUOTAS)
MAX_EVIDENCE_SPAN_CHARS = 700
MAX_EVIDENCE_SPANS_PER_CHUNK_ROLE = 2
MIN_EVIDENCE_SPAN_SCORE = 0.28
ROLE_MIN_EVIDENCE_SPAN_SCORE = {
    "primary_financial_statement_chunks": 0.28,
    "mda_explanation_chunks": 0.22,
    "segment_or_product_breakdown_chunks": 0.22,
    "risk_factor_chunks": 0.20,
    "annual_context_chunks": 0.20,
}
LATEST_FILING_COMPARISON_BASES = {
    "latest_quarter_yoy",
    "latest_ytd_yoy",
    "latest_fy_yoy",
}


class RetrievalError(ValueError):
    """Base error for retrieval failures."""


class RetrievalCompanyNotFoundError(RetrievalError):
    """Raised when retrieval is requested for an unknown company."""


@dataclass
class Candidate:
    chunk_id: int
    source_ranks: dict[str, int] = field(default_factory=dict)
    source_scores: dict[str, float] = field(default_factory=dict)
    fusion_score: float = 0.0
    rerank_score: float | None = None


@dataclass(frozen=True)
class DenseQuerySpec:
    source_name: str
    text: str
    weight: float


@dataclass(frozen=True)
class LexicalQuerySpec:
    source_name: str
    queries: tuple[str, ...]
    weight: float


@dataclass(frozen=True)
class RetrievalScope:
    company_id: int | None = None
    filing_ids: tuple[int, ...] = ()
    accession_numbers: tuple[str, ...] = ()
    form_types: tuple[str, ...] = ()
    filed_date: date | None = None
    period_end: date | None = None
    duration_class: str | None = None
    reason: str | None = None

    @property
    def latest_filing_date(self) -> date | None:
        return self.filed_date


@dataclass(frozen=True)
class EvidenceTextUnit:
    text: str
    start_char: int
    end_char: int


@dataclass(frozen=True)
class ValueMatch:
    matched: bool
    kind: str | None = None
    confidence: float = 0.0
    matched_text: str | None = None


class RetrievalService:
    def __init__(
        self,
        db: Session,
        *,
        settings: Settings | None = None,
        planner: QueryPlanner | None = None,
        embedding_provider: EmbeddingProvider | None = None,
    ) -> None:
        self._db = db
        self._settings = settings or get_settings()
        self._planner = planner or QueryPlanner()
        self._embedding_provider = embedding_provider

    def retrieve(self, request: RetrievalRequest) -> RetrievalResponse:
        started = perf_counter()
        timings: dict[str, float] = {}
        degraded: list[dict[str, str]] = []

        company = self._get_company(request.ticker)

        planned_at = perf_counter()
        plan = self._planner.plan(
            request.question,
            form_type=request.form_type,
            section=request.section,
        )
        timings["planner_ms"] = _elapsed_ms(planned_at)

        fact_at = perf_counter()
        facts: list[FinancialFact] = []
        fact_reads: list[RetrievedFinancialFactRead] = []
        metric_observations: list[MetricObservationRead] = []
        comparison_facts: list[FinancialFact] = []
        metric_comparisons: list[MetricComparisonRead] = []
        if plan.needs_financial_facts:
            fact_limit = (
                max(self._settings.retrieval_fact_candidates, 80)
                if should_build_metric_comparisons(plan)
                else self._settings.retrieval_fact_candidates
            )
            facts = self._financial_fact_candidates(
                company,
                plan,
                request,
                limit=fact_limit,
                retrieval_scope=None,
            )
            fact_reads = [
                build_retrieved_fact(fact, rank=index + 1)
                for index, fact in enumerate(facts)
            ]
            metric_observations = build_metric_observations(fact_reads, plan)
            comparison_facts = facts
            metric_comparisons = build_metric_comparisons(comparison_facts, plan)
        timings["facts_ms"] = _elapsed_ms(fact_at)

        retrieval_scope = (
            scope_from_metric_observations(company, metric_observations, plan)
            or self._retrieval_scope(company, plan, request)
        )

        dense_at = perf_counter()
        dense_sources = self._dense_candidate_sources(
            company,
            plan,
            request,
            degraded,
            retrieval_scope=retrieval_scope,
        )
        dense_candidates = aggregate_source_candidates(
            dense_sources,
            limit=self._settings.retrieval_dense_candidates,
        )
        timings["dense_ms"] = _elapsed_ms(dense_at)

        lexical_at = perf_counter()
        lexical_sources = self._lexical_candidate_sources(
            company,
            plan,
            request,
            degraded,
            retrieval_scope=retrieval_scope,
        )
        lexical_candidates = aggregate_source_candidates(
            lexical_sources,
            limit=self._settings.retrieval_lexical_candidates,
        )
        timings["lexical_ms"] = _elapsed_ms(lexical_at)

        fusion_at = perf_counter()
        fused = weighted_rrf_sources(
            [
                *dense_sources,
                *lexical_sources,
            ]
        )
        chunks_by_id = self._load_chunks([candidate.chunk_id for candidate in fused])
        evidence_limit = evidence_candidate_limit(
            plan,
            top_k=self._settings.retrieval_top_k,
        )
        ranked_for_evidence, rerank_trace = rerank_chunks(
            fused,
            chunks_by_id,
            plan=plan,
            top_k=evidence_limit,
        )
        ranked_chunks = ranked_for_evidence[: self._settings.retrieval_top_k]
        timings["fusion_rerank_ms"] = _elapsed_ms(fusion_at)

        chunk_reads = [
            build_retrieved_chunk(
                chunk,
                candidate,
                rerank_trace.get(candidate.chunk_id, {}),
                metric_keys=plan.metric_keys,
            )
            for candidate, chunk in ranked_chunks
        ]
        evidence_chunk_reads = [
            build_retrieved_chunk(
                chunk,
                candidate,
                rerank_trace.get(candidate.chunk_id, {}),
                metric_keys=plan.metric_keys,
            )
            for candidate, chunk in ranked_for_evidence
        ]
        pack_at = perf_counter()
        final_evidence_pack, evidence_pack_trace = build_final_evidence_pack(
            evidence_chunk_reads,
            metric_comparisons,
            plan,
            metric_observations=metric_observations,
            chunk_text_by_id={
                chunk.id: chunk.chunk_text for _, chunk in ranked_for_evidence
            },
        )
        if should_warn_empty_evidence_pack(plan, final_evidence_pack):
            degraded.append(
                {
                    "stage": "evidence_pack",
                    "reason": "empty_evidence_pack",
                }
            )
        timings["evidence_pack_ms"] = _elapsed_ms(pack_at)
        timings["total_ms"] = _elapsed_ms(started)

        trace = {
            "planner": plan.to_dict(),
            "candidate_counts": {
                "dense": len(dense_candidates),
                "lexical": len(lexical_candidates),
                "facts": len(facts),
                "metric_observations": len(metric_observations),
                "comparison_facts": len(comparison_facts),
                "metric_comparisons": len(metric_comparisons),
                "fused_chunks": len(fused),
                "evidence_chunk_candidates": len(evidence_chunk_reads),
                "evidence_span_candidates": sum(
                    len(items)
                    for items in evidence_pack_trace.get("span_candidates", {}).values()
                ),
                "selected_evidence_spans": len(evidence_spans_for_pack(final_evidence_pack)),
            },
            "metric_comparisons": [comparison.model_dump(mode="json") for comparison in metric_comparisons],
            "dense_query_sources": [
                {
                    "source": source_name,
                    "candidate_count": len(source_candidates),
                    "weight": weight,
                }
                for source_name, source_candidates, weight in dense_sources
            ],
            "lexical_query_sources": [
                {
                    "source": source_name,
                    "candidate_count": len(source_candidates),
                    "weight": weight,
                }
                for source_name, source_candidates, weight in lexical_sources
            ],
            "fusion": {
                str(candidate.chunk_id): {
                    "fusion_score": round(candidate.fusion_score, 6),
                    "source_ranks": candidate.source_ranks,
                    "source_scores": candidate.source_scores,
                }
                for candidate in fused
            },
            "rerank_boosts": {
                str(chunk_id): boosts for chunk_id, boosts in rerank_trace.items()
            },
            "chunk_scope": {
                "latest_filing_date": (
                    retrieval_scope.latest_filing_date.isoformat()
                    if retrieval_scope.latest_filing_date is not None
                    else None
                ),
                "filing_ids": list(retrieval_scope.filing_ids),
                "accession_numbers": list(retrieval_scope.accession_numbers),
                "form_types": list(retrieval_scope.form_types),
                "duration_class": retrieval_scope.duration_class,
                "reason": retrieval_scope.reason,
            },
            "evidence_pack": evidence_pack_trace,
            "timing_ms": timings,
            "degraded": degraded,
            "retrieval_config": {
                "vector_search_mode": self._settings.vector_search_mode,
                "embedding_provider": self._settings.embedding_provider,
                "embedding_model": self._settings.embedding_model,
                "embedding_dimensions": self._settings.embedding_dimensions,
                "embedding_input_version": self._settings.embedding_input_version,
                "dense_candidates": self._settings.retrieval_dense_candidates,
                "lexical_candidates": self._settings.retrieval_lexical_candidates,
                "fact_candidates": self._settings.retrieval_fact_candidates,
                "top_k": self._settings.retrieval_top_k,
            },
        }

        return RetrievalResponse(
            retrieval_plan=plan.to_dict(),
            retrieved_chunks=chunk_reads,
            retrieved_facts=fact_reads,
            metric_comparisons=metric_comparisons,
            final_evidence_pack=final_evidence_pack,
            source_coverage_summary=build_source_coverage_summary(
                chunk_reads,
                fact_reads,
                metric_comparisons,
                final_evidence_pack,
            ),
            retrieval_trace=trace,
        )

    def _dense_candidate_sources(
        self,
        company: Company,
        plan: RetrievalPlan,
        request: RetrievalRequest,
        degraded: list[dict[str, str]],
        *,
        retrieval_scope: RetrievalScope,
    ) -> list[tuple[str, list[tuple[int, float]], float]]:
        # dense retrieval 用的是 pgvector 的 <=> 距离，距离越小越相似
        embeddings_count = self._db.scalar(
            select(func.count())
            .select_from(ChunkEmbedding)
            .where(
                ChunkEmbedding.company_id == company.id,
                ChunkEmbedding.provider == self._settings.embedding_provider,
                ChunkEmbedding.model == self._settings.embedding_model,
                ChunkEmbedding.dimensions == self._settings.embedding_dimensions,
                ChunkEmbedding.embedding_input_version == self._settings.embedding_input_version,
            )
        )
        if not embeddings_count:
            degraded.append({"stage": "dense", "reason": "missing_embeddings"})
            return []

        dense_specs = effective_dense_query_specs(plan)
        if not dense_specs:
            degraded.append({"stage": "dense", "reason": "missing_dense_queries"})
            return []

        provider = self._get_embedding_provider()
        try:
            query_embeddings = provider.embed_texts(
                [spec.text for spec in dense_specs],
                model=self._settings.embedding_model,
                dimensions=self._settings.embedding_dimensions,
            )
        except EmbeddingProviderError as exc:
            degraded.append({"stage": "dense", "reason": str(exc)})
            return []

        if len(query_embeddings) != len(dense_specs):
            degraded.append(
                {
                    "stage": "dense",
                    "reason": "embedding_provider_returned_wrong_vector_count",
                }
            )
            return []

        sources: list[tuple[str, list[tuple[int, float]], float]] = []
        for spec, query_embedding in zip(dense_specs, query_embeddings, strict=True):
            params: dict[str, Any] = {
                "company_id": company.id,
                "provider": self._settings.embedding_provider,
                "model": self._settings.embedding_model,
                "dimensions": self._settings.embedding_dimensions,
                "embedding_input_version": self._settings.embedding_input_version,
                "query_embedding": vector_literal(query_embedding),
                "limit": self._settings.retrieval_dense_candidates,
            }
            filters = build_chunk_filter_sql(
                request,
                params,
                plan=plan,
                retrieval_scope=retrieval_scope,
            )
            sql = text(
                f"""
                SELECT ce.chunk_id, ce.embedding <=> CAST(:query_embedding AS vector) AS distance
                FROM chunk_embeddings ce
                JOIN document_chunks dc ON dc.id = ce.chunk_id
                WHERE ce.company_id = :company_id
                  AND ce.provider = :provider
                  AND ce.model = :model
                  AND ce.dimensions = :dimensions
                  AND ce.embedding_input_version = :embedding_input_version
                  {filters}
                ORDER BY distance ASC
                LIMIT :limit
                """
            )
            try:
                rows = self._db.execute(sql, params).mappings().all()
            except Exception as exc:
                degraded.append({"stage": "dense", "reason": str(exc)})
                return []
            sources.append(
                (
                    spec.source_name,
                    [(int(row["chunk_id"]), float(row["distance"])) for row in rows],
                    spec.weight,
                )
            )
        return sources

    def _lexical_candidate_sources(
        self,
        company: Company,
        plan: RetrievalPlan,
        request: RetrievalRequest,
        degraded: list[dict[str, str]],
        *,
        retrieval_scope: RetrievalScope,
    ) -> list[tuple[str, list[tuple[int, float]], float]]:
        sources: list[tuple[str, list[tuple[int, float]], float]] = []
        for spec in effective_lexical_query_specs(plan):
            best_scores: dict[int, float] = {}
            for query in spec.queries:
                params: dict[str, Any] = {
                    "company_id": company.id,
                    "query": query,
                    "limit": self._settings.retrieval_lexical_candidates,
                }
                filters = build_chunk_filter_sql(
                    request,
                    params,
                    table_alias="dc",
                    plan=plan,
                    retrieval_scope=retrieval_scope,
                )
                sql = text(
                    f"""
                    WITH q AS (SELECT websearch_to_tsquery('english', :query) AS query)
                    SELECT dc.id AS chunk_id, ts_rank_cd(dc.search_vector, q.query) AS rank
                    FROM document_chunks dc
                    JOIN filings f ON f.id = dc.filing_id
                    CROSS JOIN q
                    WHERE f.company_id = :company_id
                      AND dc.search_vector @@ q.query
                      {filters}
                    ORDER BY rank DESC
                    LIMIT :limit
                    """
                )
                try:
                    rows = self._db.execute(sql, params).mappings().all()
                except Exception as exc:
                    degraded.append({"stage": "lexical", "reason": str(exc)})
                    return []

                for row in rows:
                    chunk_id = int(row["chunk_id"])
                    rank = float(row["rank"])
                    best_scores[chunk_id] = max(best_scores.get(chunk_id, 0.0), rank)

            ordered = sorted(best_scores.items(), key=lambda item: item[1], reverse=True)
            sources.append(
                (
                    spec.source_name,
                    ordered[: self._settings.retrieval_lexical_candidates],
                    spec.weight,
                )
            )
        return sources

    def _financial_fact_candidates(
        self,
        company: Company,
        plan: RetrievalPlan,
        request: RetrievalRequest,
        *,
        limit: int | None = None,
        retrieval_scope: RetrievalScope | None = None,
    ) -> list[FinancialFact]:
        if not plan.metric_keys:
            return []

        candidate_limit = limit or self._settings.retrieval_fact_candidates
        form_types = effective_form_types(request, plan)
        duration_class = effective_duration_class(plan, retrieval_scope)
        if len(plan.metric_keys) > 1:
            per_metric_limit = max(6, candidate_limit // len(plan.metric_keys))
            facts: list[FinancialFact] = []
            for metric_key in plan.metric_keys:
                facts.extend(
                    self._financial_fact_candidates_for_metric(
                        company,
                        metric_key,
                        request,
                        limit=per_metric_limit,
                        form_types=form_types,
                        duration_class=duration_class,
                        retrieval_scope=retrieval_scope,
                    )
                )
            return facts

        return self._financial_fact_candidates_for_metric(
            company,
            plan.metric_keys[0],
            request,
            limit=candidate_limit,
            form_types=form_types,
            duration_class=duration_class,
            retrieval_scope=retrieval_scope,
        )

    def _financial_fact_candidates_for_metric(
        self,
        company: Company,
        metric_key: str,
        request: RetrievalRequest,
        *,
        limit: int,
        form_types: list[str],
        duration_class: str | None = None,
        retrieval_scope: RetrievalScope | None = None,
    ) -> list[FinancialFact]:
        fetch_limit = max(limit, limit * 5) if duration_class else limit
        statement = (
            select(FinancialFact)
            .where(
                FinancialFact.company_id == company.id,
                FinancialFact.canonical_metric_key == metric_key,
            )
            .order_by(
                FinancialFact.period_end.desc(),
                FinancialFact.filed_date.desc().nullslast(),
                FinancialFact.id.desc(),
            )
            .limit(fetch_limit)
        )
        if request.date_from is not None:
            statement = statement.where(FinancialFact.period_end >= request.date_from)
        if request.date_to is not None:
            statement = statement.where(FinancialFact.period_end <= request.date_to)
        if form_types:
            statement = statement.where(FinancialFact.form_type.in_(form_types))
        if retrieval_scope is not None:
            source_filters = []
            if retrieval_scope.filing_ids:
                source_filters.append(
                    FinancialFact.source_filing_id.in_(list(retrieval_scope.filing_ids))
                )
            if retrieval_scope.accession_numbers:
                source_filters.append(
                    FinancialFact.source_accession_number.in_(
                        list(retrieval_scope.accession_numbers)
                    )
                )
            if source_filters:
                statement = statement.where(or_(*source_filters))

        facts = list(self._db.scalars(statement).all())
        if duration_class:
            duration_matched = [
                fact for fact in facts if classify_fact_duration(fact) == duration_class
            ]
            if duration_class == "quarter":
                duration_matched.extend(
                    self._computed_q4_fact_candidates(
                        company,
                        metric_key,
                        request,
                        form_types=form_types,
                        retrieval_scope=retrieval_scope,
                        limit=max(2, limit),
                    )
                )
            if duration_matched:
                return rank_financial_facts(
                    duration_matched,
                    metric_key=metric_key,
                    duration_class=duration_class,
                    limit=limit,
                )
            return []
        return rank_financial_facts(
            facts,
            metric_key=metric_key,
            duration_class=duration_class,
            limit=limit,
        )

    def _computed_q4_fact_candidate(
        self,
        company: Company,
        metric_key: str,
        request: RetrievalRequest,
        *,
        form_types: list[str],
        retrieval_scope: RetrievalScope | None,
    ) -> FinancialFact | None:
        candidates = self._computed_q4_fact_candidates(
            company,
            metric_key,
            request,
            form_types=form_types,
            retrieval_scope=retrieval_scope,
            limit=1,
        )
        return candidates[0] if candidates else None

    def _computed_q4_fact_candidates(
        self,
        company: Company,
        metric_key: str,
        request: RetrievalRequest,
        *,
        form_types: list[str],
        retrieval_scope: RetrievalScope | None,
        limit: int,
    ) -> list[FinancialFact]:
        if form_types and "10-K" not in form_types:
            return []
        if (
            retrieval_scope is not None
            and retrieval_scope.form_types
            and "10-K" not in retrieval_scope.form_types
        ):
            return []

        fy_statement = (
            select(FinancialFact)
            .where(
                FinancialFact.company_id == company.id,
                FinancialFact.canonical_metric_key == metric_key,
                FinancialFact.form_type == "10-K",
            )
            .order_by(
                FinancialFact.period_end.desc(),
                FinancialFact.filed_date.desc().nullslast(),
                FinancialFact.id.desc(),
            )
            .limit(20)
        )
        if retrieval_scope is not None and retrieval_scope.filing_ids:
            fy_statement = fy_statement.where(
                FinancialFact.source_filing_id.in_(list(retrieval_scope.filing_ids))
            )
        elif retrieval_scope is not None and retrieval_scope.accession_numbers:
            fy_statement = fy_statement.where(
                FinancialFact.source_accession_number.in_(
                    list(retrieval_scope.accession_numbers)
                )
            )
        if request.date_from is not None:
            fy_statement = fy_statement.where(FinancialFact.period_end >= request.date_from)
        if request.date_to is not None:
            fy_statement = fy_statement.where(FinancialFact.period_end <= request.date_to)

        fy_facts = [
            fact for fact in self._db.scalars(fy_statement).all()
            if classify_fact_duration(fact) == "fy"
        ]
        if not fy_facts:
            return []

        computed_facts: list[FinancialFact] = []
        for fy_fact in rank_financial_facts(
            fy_facts,
            metric_key=metric_key,
            duration_class="fy",
            limit=max(limit * 3, limit),
        ):
            q4_fact = self._computed_q4_from_fy_fact(
                company,
                metric_key,
                fy_fact,
                request,
            )
            if q4_fact is not None:
                computed_facts.append(q4_fact)

        return rank_financial_facts(
            computed_facts,
            metric_key=metric_key,
            duration_class="quarter",
            limit=limit,
        )

    def _computed_q4_from_fy_fact(
        self,
        company: Company,
        metric_key: str,
        fy_fact: FinancialFact,
        request: RetrievalRequest,
    ) -> FinancialFact | None:
        ytd_statement = (
            select(FinancialFact)
            .where(
                FinancialFact.company_id == company.id,
                FinancialFact.canonical_metric_key == metric_key,
                FinancialFact.period_end < fy_fact.period_end,
                FinancialFact.unit == fy_fact.unit,
            )
            .order_by(
                FinancialFact.period_end.desc(),
                FinancialFact.filed_date.desc().nullslast(),
                FinancialFact.id.desc(),
            )
            .limit(40)
        )
        if fy_fact.fact_fiscal_year is not None:
            ytd_statement = ytd_statement.where(
                FinancialFact.fact_fiscal_year == fy_fact.fact_fiscal_year
            )
        elif fy_fact.source_fiscal_year is not None:
            ytd_statement = ytd_statement.where(
                FinancialFact.source_fiscal_year == fy_fact.source_fiscal_year
            )
        ytd_candidates = [
            fact for fact in self._db.scalars(ytd_statement).all()
            if classify_fact_duration(fact) == "ytd"
        ]
        if not ytd_candidates:
            return None
        ytd_fact = rank_financial_facts(
            ytd_candidates,
            metric_key=metric_key,
            duration_class="ytd",
            limit=1,
        )[0]
        if (
            fy_fact.period_start is not None
            and ytd_fact.period_start is not None
            and fy_fact.period_start != ytd_fact.period_start
        ):
            return None

        computed = FinancialFact(
            id=negative_computed_fact_id(fy_fact, ytd_fact),
            company_id=company.id,
            canonical_metric_key=metric_key,
            taxonomy_tag=fy_fact.taxonomy_tag,
            label=f"{fy_fact.label} (computed Q4)",
            period_start=ytd_fact.period_end + timedelta(days=1),
            period_end=fy_fact.period_end,
            source_fiscal_year=fy_fact.source_fiscal_year,
            fact_fiscal_year=fy_fact.fact_fiscal_year,
            fiscal_period="Q4",
            form_type=fy_fact.form_type,
            filed_date=fy_fact.filed_date,
            unit=fy_fact.unit,
            value=Decimal(fy_fact.value) - Decimal(ytd_fact.value),
            source_accession_number=fy_fact.source_accession_number,
            source_filing_id=fy_fact.source_filing_id,
            source_filing_url=fy_fact.source_filing_url,
            source_fact_id=(
                f"computed:q4:{fy_fact.source_fact_id}:{ytd_fact.source_fact_id}"
            ),
            is_computed=True,
            calculation_notes=(
                "Computed Q4 value as FY fact minus nine-month YTD fact; "
                f"fy_fact_id={fy_fact.id}; ytd_fact_id={ytd_fact.id}; "
                f"ytd_accession={ytd_fact.source_accession_number}"
            ),
        )
        computed._component_facts = (fy_fact, ytd_fact)
        computed._calculation_expression = (
            f"{metric_key} FY - nine-month YTD"
        )
        return computed

    def _retrieval_scope(
        self,
        company: Company,
        plan: RetrievalPlan,
        request: RetrievalRequest,
    ) -> RetrievalScope:
        reason = latest_filing_scope_reason(plan)
        if reason is None:
            return RetrievalScope(
                company_id=company.id,
                duration_class=effective_duration_class(plan),
            )

        statement = (
            select(Filing)
            .where(Filing.company_id == company.id)
            .order_by(
                Filing.filing_date.desc(),
                form_priority_sort_expression(plan),
                Filing.id.desc(),
            )
            .limit(1)
        )
        form_types = effective_form_types(request, plan)
        if form_types:
            statement = statement.where(Filing.form_type.in_(form_types))
        if request.date_from is not None:
            statement = statement.where(Filing.filing_date >= request.date_from)
        if request.date_to is not None:
            statement = statement.where(Filing.filing_date <= request.date_to)
        if request.section is not None and request.section.strip():
            statement = statement.join(DocumentChunk, DocumentChunk.filing_id == Filing.id)
            statement = statement.where(
                DocumentChunk.section_label.ilike(f"%{request.section.strip()}%")
            )

        filing = self._db.scalar(statement)
        if filing is None:
            return RetrievalScope(
                company_id=company.id,
                duration_class=effective_duration_class(plan),
                reason=f"{reason}:no_matching_filing",
            )
        return RetrievalScope(
            company_id=company.id,
            filing_ids=(filing.id,),
            accession_numbers=(filing.accession_number,),
            form_types=(filing.form_type,),
            filed_date=filing.filing_date,
            period_end=filing.report_date,
            duration_class=effective_duration_class(plan),
            reason=reason,
        )

    def _load_chunks(self, chunk_ids: list[int]) -> dict[int, DocumentChunk]:
        if not chunk_ids:
            return {}
        statement = select(DocumentChunk).where(DocumentChunk.id.in_(chunk_ids))
        return {chunk.id: chunk for chunk in self._db.scalars(statement).all()}

    def _get_company(self, ticker: str) -> Company:
        try:
            normalized_ticker = normalize_ticker(ticker)
        except CompanyLookupError as exc:
            raise RetrievalError(str(exc)) from exc

        statement = select(Company).where(Company.ticker == normalized_ticker)
        company = self._db.scalar(statement)
        if company is None:
            raise RetrievalCompanyNotFoundError(f"Company {normalized_ticker} was not found.")
        return company

    def _get_embedding_provider(self) -> EmbeddingProvider:
        if self._embedding_provider is None:
            self._embedding_provider = build_embedding_provider(self._settings)
        return self._embedding_provider


def effective_dense_query_specs(plan: RetrievalPlan) -> list[DenseQuerySpec]:
    raw_specs: list[dict[str, Any]]
    if plan.dense_query_specs:
        raw_specs = plan.dense_query_specs
    else:
        raw_specs = [
            {"role": f"query_{index + 1}", "text": query, "weight": 1.0}
            for index, query in enumerate(plan.dense_queries)
        ]

    specs: list[DenseQuerySpec] = []
    seen_texts: set[str] = set()
    seen_sources: dict[str, int] = {}
    for index, raw_spec in enumerate(raw_specs):
        text_value = " ".join(str(raw_spec.get("text", "")).split())
        if not text_value or text_value in seen_texts:
            continue
        seen_texts.add(text_value)

        source_role = normalize_source_role(
            str(raw_spec.get("role") or f"query_{index + 1}")
        )
        seen_sources[source_role] = seen_sources.get(source_role, 0) + 1
        if seen_sources[source_role] > 1:
            source_role = f"{source_role}_{seen_sources[source_role]}"

        specs.append(
            DenseQuerySpec(
                source_name=f"dense:{source_role}",
                text=text_value,
                weight=coerce_source_weight(raw_spec.get("weight")),
            )
        )
    return specs


def effective_lexical_query_specs(plan: RetrievalPlan) -> list[LexicalQuerySpec]:
    raw_specs = getattr(plan, "lexical_query_specs", None) or []
    specs: list[LexicalQuerySpec] = []
    seen_sources: dict[str, int] = {}
    if raw_specs:
        for index, raw_spec in enumerate(raw_specs):
            raw_queries = raw_spec.get("queries") if isinstance(raw_spec, dict) else None
            if isinstance(raw_queries, str):
                raw_query_items = [raw_queries]
            elif isinstance(raw_queries, list):
                raw_query_items = raw_queries
            else:
                raw_query_items = []
            queries = tuple(
                dict.fromkeys(
                    " ".join(str(query).split())
                    for query in raw_query_items
                    if str(query).strip()
                )
            )
            if not queries:
                continue
            source_role = normalize_source_role(
                str(raw_spec.get("role") or f"query_{index + 1}")
            )
            seen_sources[source_role] = seen_sources.get(source_role, 0) + 1
            if seen_sources[source_role] > 1:
                source_role = f"{source_role}_{seen_sources[source_role]}"
            specs.append(
                LexicalQuerySpec(
                    source_name=f"lexical:{source_role}",
                    queries=queries,
                    weight=coerce_source_weight(raw_spec.get("weight", 1.0)),
                )
            )
    if specs:
        return specs

    queries = tuple(
        dict.fromkeys(
            " ".join(query.split())
            for query in plan.lexical_queries
            if query.strip()
        )
    )
    if not queries:
        return []
    return [
        LexicalQuerySpec(
            source_name="lexical",
            queries=queries,
            weight=LEXICAL_WEIGHT,
        )
    ]


def scope_from_metric_observations(
    company: Company,
    observations: list[MetricObservationRead],
    plan: RetrievalPlan,
) -> RetrievalScope | None:
    if not observations:
        return None

    filing_ids: list[int] = []
    accession_numbers: list[str] = []
    form_types: list[str] = []
    filed_dates: list[date] = []
    period_ends: list[date] = []

    def add_source(
        *,
        filing_id: int | None,
        accession_number: str | None,
        form_type: str | None,
        filed_date: date | None,
        period_end: date | None,
    ) -> None:
        if filing_id is not None:
            filing_ids.append(filing_id)
        if accession_number:
            accession_numbers.append(accession_number)
        if form_type:
            form_types.append(form_type)
        if filed_date is not None:
            filed_dates.append(filed_date)
        if period_end is not None:
            period_ends.append(period_end)

    for observation in observations:
        add_source(
            filing_id=observation.source_filing_id,
            accession_number=observation.source_accession_number,
            form_type=observation.form_type,
            filed_date=observation.filed_date,
            period_end=observation.period_end,
        )
        for component in observation.component_observations:
            add_source(
                filing_id=component.source_filing_id,
                accession_number=component.source_accession_number,
                form_type=component.form_type,
                filed_date=component.filed_date,
                period_end=component.period_end,
            )

    filing_ids_tuple = tuple(dict.fromkeys(filing_ids))
    accession_numbers_tuple = tuple(dict.fromkeys(accession_numbers))
    if not filing_ids_tuple and not accession_numbers_tuple:
        return None

    return RetrievalScope(
        company_id=company.id,
        filing_ids=filing_ids_tuple,
        accession_numbers=accession_numbers_tuple,
        form_types=tuple(dict.fromkeys(form_types)),
        filed_date=max(filed_dates) if filed_dates else None,
        period_end=max(period_ends) if period_ends else None,
        duration_class=effective_duration_class(plan),
        reason="metric_observation",
    )


def normalize_source_role(role: str) -> str:
    normalized = re.sub(r"[^a-z0-9_]+", "_", role.strip().lower())
    normalized = re.sub(r"_+", "_", normalized).strip("_")
    return normalized or "query"


def coerce_source_weight(value: Any) -> float:
    try:
        weight = float(value)
    except (TypeError, ValueError):
        return 1.0
    if weight <= 0:
        return 1.0
    return weight


def rank_financial_facts(
    facts: list[FinancialFact],
    *,
    metric_key: str,
    duration_class: str | None,
    limit: int,
) -> list[FinancialFact]:
    deduped = dedupe_financial_facts(facts, metric_key=metric_key)
    return sorted(
        deduped,
        key=lambda fact: (
            duration_class is None or classify_fact_duration(fact) == duration_class,
            fact.period_end,
            fact.filed_date or date.min,
            financial_fact_quality_score(fact, metric_key=metric_key),
            fact.source_filing_id is not None or bool(fact.source_accession_number),
            not fact.is_computed,
            fact.id,
        ),
        reverse=True,
    )[:limit]


def dedupe_financial_facts(
    facts: list[FinancialFact],
    *,
    metric_key: str,
) -> list[FinancialFact]:
    best_by_key: dict[tuple[Any, ...], FinancialFact] = {}
    for fact in facts:
        key = (
            fact.canonical_metric_key,
            fact.period_start,
            fact.period_end,
            classify_fact_duration(fact),
            fact.source_filing_id,
            fact.source_accession_number,
            fact.unit,
            Decimal(fact.value),
        )
        current = best_by_key.get(key)
        if current is None or financial_fact_quality_score(
            fact,
            metric_key=metric_key,
        ) > financial_fact_quality_score(current, metric_key=metric_key):
            best_by_key[key] = fact
    return list(best_by_key.values())


def financial_fact_quality_score(fact: FinancialFact, *, metric_key: str) -> int:
    return _metric_fact_quality_score(
        metric_key=metric_key,
        taxonomy_tag=fact.taxonomy_tag,
        label=fact.label,
        unit=fact.unit,
    )


def retrieved_fact_quality_score(fact: RetrievedFinancialFactRead) -> int:
    return _metric_fact_quality_score(
        metric_key=fact.canonical_metric_key,
        taxonomy_tag=fact.taxonomy_tag,
        label=fact.label,
        unit=fact.unit,
    )


def _metric_fact_quality_score(
    *,
    metric_key: str,
    taxonomy_tag: str | None,
    label: str | None,
    unit: str | None,
) -> int:
    profile = get_metric_profile(metric_key)
    if profile is None:
        return 0

    score = 0
    normalized_tag = normalize_fact_tag(taxonomy_tag or "")
    preferred_tags = {normalize_fact_tag(tag) for tag in profile.fact_tags}
    if normalized_tag and normalized_tag in preferred_tags:
        score += 14

    label_text = normalize_match_text(label or "")
    label_compact = " ".join(label_text.split())
    preferred_labels = tuple(normalize_match_text(item) for item in profile.preferred_labels)
    if label_compact in preferred_labels:
        score += 12
    elif _contains_any(label_compact, preferred_labels):
        score += 6

    if _contains_any(label_compact, profile.consolidated_terms):
        score += 4
    if profile.preferred_units and (unit or "").upper() in profile.preferred_units:
        score += 4
    if not _contains_any(label_compact, profile.segment_terms):
        score += 6
    else:
        score -= 16
    if _contains_any(label_compact, profile.negative_terms):
        score -= 12
    return score


def normalize_fact_tag(tag: str) -> str:
    return tag.split(":")[-1].lower()


def aggregate_source_candidates(
    sources: list[tuple[str, list[tuple[int, float]], float]],
    *,
    limit: int,
) -> list[tuple[int, float]]:
    fused = weighted_rrf_sources(sources)
    return [
        (candidate.chunk_id, candidate.fusion_score)
        for candidate in fused[:limit]
    ]


def weighted_rrf(
    dense_candidates: list[tuple[int, float]],
    lexical_candidates: list[tuple[int, float]],
    *,
    dense_weight: float = DENSE_WEIGHT,
    lexical_weight: float = LEXICAL_WEIGHT,
) -> list[Candidate]:
    return weighted_rrf_sources(
        [
            ("dense", dense_candidates, dense_weight),
            ("lexical", lexical_candidates, lexical_weight),
        ]
    )


def weighted_rrf_sources(
    sources: list[tuple[str, list[tuple[int, float]], float]],
) -> list[Candidate]:
    candidates: dict[int, Candidate] = {}
    for source_name, source_candidates, weight in sources:
        if weight <= 0:
            continue
        for rank_index, (chunk_id, source_score) in enumerate(source_candidates, start=1):
            candidate = candidates.setdefault(chunk_id, Candidate(chunk_id=chunk_id))
            candidate.source_ranks[source_name] = rank_index
            candidate.source_scores[source_name] = round(float(source_score), 6)
            candidate.fusion_score += weight / (RRF_K + rank_index)

    return sorted(candidates.values(), key=lambda candidate: candidate.fusion_score, reverse=True)


def rerank_chunks(
    candidates: list[Candidate],
    chunks_by_id: dict[int, DocumentChunk],
    *,
    plan: RetrievalPlan,
    top_k: int,
) -> tuple[list[tuple[Candidate, DocumentChunk]], dict[int, dict[str, float]]]:
    available = [
        (candidate, chunks_by_id[candidate.chunk_id])
        for candidate in candidates
        if candidate.chunk_id in chunks_by_id
    ]
    latest_date = max((chunk.filing_date for _, chunk in available), default=None)
    max_fusion_score = max(
        (candidate.fusion_score for candidate, _ in available),
        default=0.0,
    )
    trace: dict[int, dict[str, float]] = {}

    def score(item: tuple[Candidate, DocumentChunk]) -> float:
        candidate, chunk = item
        boosts = metadata_boosts(chunk, plan=plan, latest_date=latest_date)
        trace[chunk.id] = boosts
        normalized_fusion = (
            candidate.fusion_score / max_fusion_score
            if max_fusion_score > 0
            else 0.0
        )
        heuristic_score = max(-1.0, min(sum(boosts.values()), 1.0))
        candidate.rerank_score = (0.75 * normalized_fusion) + (0.25 * heuristic_score)
        return candidate.rerank_score

    return sorted(available, key=score, reverse=True)[:top_k], trace


def metadata_boosts(
    chunk: DocumentChunk,
    *,
    plan: RetrievalPlan,
    latest_date: date | None,
) -> dict[str, float]:
    boosts: dict[str, float] = {}
    section_label = normalize_match_text(chunk.section_label)
    text_value = f"{section_label}\n{chunk.chunk_text}".lower()
    if any(normalize_match_text(section) in section_label for section in plan.target_sections):
        boosts["section_match"] = 0.15
    if plan.time_scope == "latest" and latest_date is not None and chunk.filing_date == latest_date:
        boosts["latest_filing"] = 0.10
    if "8-K" not in plan.forms and chunk.form_type in {"10-K", "10-Q"}:
        boosts["form_priority"] = form_priority_boost(chunk, plan)
    if plan.metric_keys and chunk.has_table:
        boosts["table_metric_context"] = 0.03
    if plan.question_type == "broad_comparison":
        boosts.update(broad_comparison_text_boosts(section_label, text_value))
    boosts.update(metric_text_boosts(chunk, plan.metric_keys))
    return boosts


def normalize_match_text(text_value: str) -> str:
    return (
        text_value.lower()
        .replace("’", "'")
        .replace("‘", "'")
        .replace("“", '"')
        .replace("”", '"')
    )


def broad_comparison_text_boosts(
    section_label: str,
    text_value: str,
) -> dict[str, float]:
    boosts: dict[str, float] = {}
    if is_broad_comparison_noise_text(section_label, text_value):
        boosts["broad_change_noise"] = -0.16
    if is_mda_section_text(section_label) and has_change_explanation_context(text_value):
        boosts["mda_change_explanation"] = 0.14
    if has_business_breakdown_context(text_value):
        boosts["business_breakdown_context"] = 0.08
    if has_margin_or_profit_driver_context(text_value):
        boosts["margin_profit_driver_context"] = 0.08
    return boosts


def is_mda_section_text(section_label: str) -> bool:
    return (
        "management" in section_label
        or "discussion and analysis" in section_label
        or "md&a" in section_label
        or "item 7" in section_label
        or "item 2" in section_label
    )


def is_broad_comparison_noise_text(section_label: str, text_value: str) -> bool:
    return _contains_any(
        f"{section_label}\n{text_value}",
        (
            "changes in and disagreements with accountants",
            "controls and procedures",
            "control system",
            "exhibit index",
            "exhibit number",
            "financial statement schedules",
            "summary of significant accounting policies",
        ),
    )


def has_change_explanation_context(text_value: str) -> bool:
    return _contains_any(
        text_value,
        (
            "products and services performance",
            "segment operating performance",
            "net sales increased",
            "net sales decreased",
            "gross margin percentage increased",
            "gross margin percentage decreased",
            "operating income increased",
            "operating income decreased",
            "net income increased",
            "net income decreased",
            "primarily due to",
            "due to higher",
            "due to lower",
            "compared to",
            "compared with",
            "year-over-year",
        ),
    )


def has_business_breakdown_context(text_value: str) -> bool:
    return _contains_any(
        text_value,
        (
            "products and services performance",
            "segment operating performance",
            "reportable segment",
            "net sales by category",
            "net sales by reportable segment",
            "americas",
            "europe",
            "greater china",
            "japan",
            "rest of asia pacific",
            "iphone",
            "ipad",
            "mac",
            "services",
        ),
    )


def has_margin_or_profit_driver_context(text_value: str) -> bool:
    return _contains_any(
        text_value,
        (
            "gross margin",
            "gross margin percentage",
            "operating income",
            "net income",
            "cost of sales",
            "operating expenses",
            "research and development",
            "selling, general and administrative",
            "effective tax rate",
        ),
    ) and _contains_any(
        text_value,
        (
            "increased",
            "decreased",
            "higher",
            "lower",
            "compared to",
            "compared with",
            "primarily due to",
            "due to",
        ),
    )


def metric_text_boosts(chunk: DocumentChunk, metric_keys: list[str]) -> dict[str, float]:
    if not metric_keys:
        return {}

    text_value = f"{chunk.section_label}\n{chunk.chunk_text}".lower()
    boosts: dict[str, float] = {}

    for metric_key in metric_keys:
        profile = get_metric_profile(metric_key)
        if profile is None:
            generic_phrase = metric_key.replace("_", " ")
            if generic_phrase in text_value:
                boosts["weak_metric_match"] = max(boosts.get("weak_metric_match", 0.0), 0.01)
            continue

        has_strong_match = _contains_any(text_value, profile.strong_terms)
        has_weak_match = _contains_any(text_value, profile.weak_terms)
        has_statement_match = _contains_any(text_value, profile.statement_terms)

        if has_strong_match:
            boosts["strong_metric_match"] = max(boosts.get("strong_metric_match", 0.0), 0.08)
        if has_statement_match and (has_strong_match or has_weak_match):
            boosts["statement_context_match"] = max(
                boosts.get("statement_context_match", 0.0),
                0.06,
            )
        if not has_strong_match and not has_statement_match and has_weak_match:
            boosts["weak_metric_match"] = max(boosts.get("weak_metric_match", 0.0), 0.01)
        if _contains_any(text_value, profile.negative_terms):
            boosts["negative_metric_context"] = min(
                boosts.get("negative_metric_context", 0.0),
                -0.07,
            )

    return boosts


def build_retrieved_chunk(
    chunk: DocumentChunk,
    candidate: Candidate,
    boosts: dict[str, float],
    *,
    metric_keys: list[str] | None = None,
) -> RetrievedChunkRead:
    return RetrievedChunkRead(
        evidence_id=f"chunk:{chunk.id}",
        chunk_id=chunk.id,
        filing_id=chunk.filing_id,
        section_id=chunk.section_id,
        score=round(
            candidate.rerank_score
            if candidate.rerank_score is not None
            else candidate.fusion_score + sum(boosts.values()),
            6,
        ),
        fusion_score=round(candidate.fusion_score, 6),
        source_ranks=candidate.source_ranks,
        rerank_boosts={key: round(value, 6) for key, value in boosts.items()},
        snippet=make_snippet(chunk.chunk_text, metric_keys=metric_keys or []),
        form_type=chunk.form_type,
        filing_date=chunk.filing_date,
        section_label=chunk.section_label,
        sec_url=chunk.sec_url,
        accession_number=chunk.accession_number,
        start_page=chunk.start_page,
        end_page=chunk.end_page,
        has_table=chunk.has_table,
    )


def build_retrieved_fact(fact: FinancialFact, *, rank: int) -> RetrievedFinancialFactRead:
    duration_class = classify_fact_duration(fact)
    component_facts = [
        build_metric_observation_component(component)
        for component in getattr(fact, "_component_facts", ())
    ]
    return RetrievedFinancialFactRead(
        evidence_id=f"financial_fact:{fact.id}",
        fact_id=fact.id,
        score=round(FACT_WEIGHT / (RRF_K + rank), 6),
        canonical_metric_key=fact.canonical_metric_key,
        taxonomy_tag=fact.taxonomy_tag,
        label=fact.label,
        period_start=fact.period_start,
        period_end=fact.period_end,
        duration_class=duration_class,
        period_label=format_fact_period_label(fact, duration_class),
        source_fiscal_year=fact.source_fiscal_year,
        fact_fiscal_year=fact.fact_fiscal_year,
        fiscal_period=fact.fiscal_period,
        form_type=fact.form_type,
        filed_date=fact.filed_date,
        unit=fact.unit,
        value=fact.value,
        source_accession_number=fact.source_accession_number,
        source_filing_id=fact.source_filing_id,
        source_filing_url=fact.source_filing_url,
        source_fact_id=fact.source_fact_id,
        is_computed=fact.is_computed,
        calculation_notes=fact.calculation_notes,
        calculation_expression=getattr(fact, "_calculation_expression", None),
        component_fact_ids=[component.fact_id for component in component_facts],
        component_facts=component_facts,
    )


def build_metric_observation_component(fact: FinancialFact) -> MetricObservationComponentRead:
    duration_class = classify_fact_duration(fact)
    return MetricObservationComponentRead(
        evidence_id=f"financial_fact:{fact.id}",
        fact_id=fact.id,
        canonical_metric_key=fact.canonical_metric_key,
        value=fact.value,
        unit=fact.unit,
        display_value=format_metric_observation_value(fact.value, fact.unit),
        period_start=fact.period_start,
        period_end=fact.period_end,
        duration_class=duration_class,
        fiscal_period=fact.fiscal_period,
        form_type=fact.form_type,
        filed_date=fact.filed_date,
        source_filing_id=fact.source_filing_id,
        source_accession_number=fact.source_accession_number,
        source_filing_url=fact.source_filing_url,
        source_fact_id=fact.source_fact_id,
    )


def build_metric_observations(
    facts: list[RetrievedFinancialFactRead],
    plan: RetrievalPlan,
) -> list[MetricObservationRead]:
    if not plan.metric_keys:
        return []

    duration_class = effective_duration_class(plan)
    observations: list[MetricObservationRead] = []
    seen_metric_keys: set[str] = set()
    for fact in sorted(
        facts,
        key=lambda item: metric_observation_rank_key(item, duration_class),
        reverse=True,
    ):
        if fact.canonical_metric_key in seen_metric_keys:
            continue
        if duration_class is not None and fact.duration_class != duration_class:
            continue
        observations.append(build_metric_observation(fact, duration_class=duration_class))
        seen_metric_keys.add(fact.canonical_metric_key)
    return observations


def metric_observation_rank_key(
    fact: RetrievedFinancialFactRead,
    duration_class: str | None,
) -> tuple[Any, ...]:
    return (
        duration_class is None or fact.duration_class == duration_class,
        fact.period_end,
        fact.filed_date or date.min,
        retrieved_fact_quality_score(fact),
        fact.source_filing_id is not None or bool(fact.source_accession_number),
        not fact.is_computed,
        fact.fact_id,
    )


def build_metric_observation(
    fact: RetrievedFinancialFactRead,
    *,
    duration_class: str | None,
) -> MetricObservationRead:
    confidence = 0.82
    if duration_class is None or fact.duration_class == duration_class:
        confidence += 0.08
    if fact.source_filing_id is not None or fact.source_accession_number:
        confidence += 0.05
    if fact.is_computed:
        confidence -= 0.08
    return MetricObservationRead(
        evidence_id=f"metric_observation:{fact.fact_id}",
        canonical_metric_key=fact.canonical_metric_key,
        value=fact.value,
        unit=fact.unit,
        display_value=format_metric_observation_value(fact.value, fact.unit),
        period_start=fact.period_start,
        period_end=fact.period_end,
        duration_class=fact.duration_class,
        fiscal_period=fact.fiscal_period,
        form_type=fact.form_type,
        filed_date=fact.filed_date,
        source_filing_id=fact.source_filing_id,
        source_accession_number=fact.source_accession_number,
        source_filing_url=fact.source_filing_url,
        source_fact_id=fact.source_fact_id,
        source_fact_evidence_id=fact.evidence_id,
        is_computed=fact.is_computed,
        calculation_expression=fact.calculation_expression,
        component_fact_ids=fact.component_fact_ids,
        component_observations=fact.component_facts,
        confidence=round(max(0.0, min(confidence, 1.0)), 3),
    )


def format_metric_observation_value(value: Decimal, unit: str) -> str:
    abs_value = abs(value)
    prefix = "$" if unit.upper() in {"USD", "US_DOLLAR", "USDOLLARS"} else ""
    if abs_value >= Decimal("1000000000"):
        return f"{prefix}{format_scaled_decimal(value / Decimal('1000000000'))}B"
    if abs_value >= Decimal("1000000"):
        return f"{prefix}{format_scaled_decimal(value / Decimal('1000000'))}M"
    if abs_value >= Decimal("1000"):
        return f"{prefix}{format_scaled_decimal(value / Decimal('1000'))}K"
    return f"{prefix}{format_scaled_decimal(value)}"


def format_scaled_decimal(value: Decimal) -> str:
    quantized = value.quantize(Decimal("0.01"))
    return f"{quantized:,.2f}".rstrip("0").rstrip(".")


def build_metric_comparisons(
    facts: list[FinancialFact],
    plan: RetrievalPlan,
) -> list[MetricComparisonRead]:
    if not should_build_metric_comparisons(plan):
        return []

    requested_bases = plan.comparison_candidates
    if not requested_bases and plan.comparison_basis not in {"none", "ambiguous"}:
        requested_bases = [plan.comparison_basis]

    comparisons: list[MetricComparisonRead] = []
    for metric_key in plan.metric_keys:
        metric_facts = [fact for fact in facts if fact.canonical_metric_key == metric_key]
        for basis in requested_bases:
            pair = find_comparison_pair(metric_facts, basis)
            if pair is None:
                continue
            current, prior = pair
            comparisons.append(build_metric_comparison(metric_key, basis, current, prior))
    return comparisons


def select_evidence_spans_for_chunk(
    chunk: RetrievedChunkRead,
    role: str,
    plan: RetrievalPlan,
    *,
    chunk_text_by_id: dict[int, str] | None = None,
    metric_observations: list[MetricObservationRead] | None = None,
    max_spans: int = MAX_EVIDENCE_SPANS_PER_CHUNK_ROLE,
) -> list[EvidenceSpanRead]:
    text_value = (
        chunk_text_by_id.get(chunk.chunk_id, chunk.snippet)
        if chunk_text_by_id
        else chunk.snippet
    )
    candidates: list[tuple[float, int, EvidenceTextUnit, list[str], str]] = []
    seen_units: set[str] = set()
    min_score = ROLE_MIN_EVIDENCE_SPAN_SCORE.get(role, MIN_EVIDENCE_SPAN_SCORE)
    for unit in split_evidence_units(text_value):
        key = evidence_span_text_key(unit.text)
        if key in seen_units:
            continue
        seen_units.add(key)
        score, reasons, support_kind = score_evidence_text_unit(
            unit.text,
            chunk,
            role,
            plan,
            metric_observations=metric_observations,
        )
        if score < min_score:
            continue
        candidates.append(
            (score, -(unit.end_char - unit.start_char), unit, reasons, support_kind)
        )

    selected = sorted(
        candidates,
        key=lambda item: (item[0], item[1], -item[2].start_char),
        reverse=True,
    )
    return [
        build_evidence_span_read(
            chunk,
            role,
            unit,
            score=score,
            reasons=reasons,
            support_kind=support_kind,
        )
        for score, _, unit, reasons, support_kind in selected[:max_spans]
    ]


def build_selected_evidence_spans(
    selected_by_role: dict[str, list[RetrievedChunkRead]],
    span_candidates_by_chunk_role: dict[tuple[int, str], list[EvidenceSpanRead]],
    plan: RetrievalPlan,
    *,
    chunk_text_by_id: dict[int, str] | None = None,
    metric_observations: list[MetricObservationRead] | None = None,
) -> tuple[dict[str, list[EvidenceSpanRead]], dict[str, Any]]:
    selected_by_span_role: dict[str, list[EvidenceSpanRead]] = {
        role: [] for role in EVIDENCE_PACK_CHUNK_ROLE_ORDER
    }
    skipped: list[dict[str, str]] = []
    seen_span_texts: set[tuple[str, str]] = set()

    for role in EVIDENCE_PACK_CHUNK_ROLE_ORDER:
        quota = EVIDENCE_PACK_SPAN_QUOTAS[role]
        for chunk in selected_by_role[role]:
            spans = span_candidates_by_chunk_role.get((chunk.chunk_id, role))
            if spans is None:
                spans = select_evidence_spans_for_chunk(
                    chunk,
                    role,
                    plan,
                    chunk_text_by_id=chunk_text_by_id,
                    metric_observations=metric_observations,
                )
            if not spans:
                skipped.append(
                    {
                        "evidence_id": chunk.evidence_id,
                        "role": role,
                        "reason": "no_qualifying_spans",
                    }
                )
                continue

            for span in spans:
                if len(selected_by_span_role[role]) >= quota:
                    skipped.append(
                        {
                            "evidence_id": span.evidence_id,
                            "role": role,
                            "reason": "span_quota_full",
                        }
                    )
                    continue

                span_key = (role, evidence_span_text_key(span.text))
                if span_key in seen_span_texts:
                    skipped.append(
                        {
                            "evidence_id": span.evidence_id,
                            "role": role,
                            "reason": "duplicate_span_text",
                        }
                    )
                    continue

                selected_by_span_role[role].append(span)
                seen_span_texts.add(span_key)

    return selected_by_span_role, {"skipped": skipped}


def build_evidence_span_read(
    chunk: RetrievedChunkRead,
    role: str,
    unit: EvidenceTextUnit,
    *,
    score: float,
    reasons: list[str],
    support_kind: str,
) -> EvidenceSpanRead:
    return EvidenceSpanRead(
        evidence_id=f"span:{chunk.chunk_id}:{role}:{unit.start_char}:{unit.end_char}",
        chunk_id=chunk.chunk_id,
        source_chunk_evidence_id=chunk.evidence_id,
        role=role,
        score=round(score, 6),
        support_kind=support_kind,
        text=truncate_evidence_span_text(unit.text),
        start_char=unit.start_char,
        end_char=unit.end_char,
        reasons=reasons,
        form_type=chunk.form_type,
        filing_date=chunk.filing_date,
        section_label=chunk.section_label,
        sec_url=chunk.sec_url,
        accession_number=chunk.accession_number,
        start_page=chunk.start_page,
        end_page=chunk.end_page,
    )


def split_evidence_units(text_value: str) -> list[EvidenceTextUnit]:
    units: list[EvidenceTextUnit] = []
    add_text_units_from_lines(text_value, units)
    add_text_units_from_paragraphs(text_value, units)
    add_text_units_from_sentences(text_value, units)

    deduped: list[EvidenceTextUnit] = []
    seen: set[str] = set()
    for unit in units:
        key = evidence_span_text_key(unit.text)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(unit)
    return deduped


def add_text_units_from_lines(text_value: str, units: list[EvidenceTextUnit]) -> None:
    offset = 0
    for line in text_value.splitlines(keepends=True):
        stripped = line.strip()
        if stripped:
            start = offset + line.find(stripped)
            add_evidence_text_unit(units, text_value, start, start + len(stripped))
        offset += len(line)


def add_text_units_from_paragraphs(text_value: str, units: list[EvidenceTextUnit]) -> None:
    for match in re.finditer(r"\S[\s\S]*?(?=(?:\r?\n\s*){2,}\S|\Z)", text_value):
        start, end = trim_text_bounds(text_value, match.start(), match.end())
        add_evidence_text_unit(units, text_value, start, end)


def add_text_units_from_sentences(text_value: str, units: list[EvidenceTextUnit]) -> None:
    for match in re.finditer(r"[^.!?;\n]+(?:[.!?;]+|$)", text_value):
        start, end = trim_text_bounds(text_value, match.start(), match.end())
        add_evidence_text_unit(units, text_value, start, end)


def add_evidence_text_unit(
    units: list[EvidenceTextUnit],
    text_value: str,
    start: int,
    end: int,
) -> None:
    if start >= end:
        return
    raw = text_value[start:end]
    normalized = normalize_evidence_span_text(raw)
    if len(normalized) < 24:
        return
    if len(normalized) > MAX_EVIDENCE_SPAN_CHARS * 2:
        for window_start, window_end in split_long_evidence_unit(text_value, start, end):
            add_evidence_text_unit(units, text_value, window_start, window_end)
        return
    units.append(EvidenceTextUnit(text=normalized, start_char=start, end_char=end))


def split_long_evidence_unit(
    text_value: str,
    start: int,
    end: int,
) -> list[tuple[int, int]]:
    windows: list[tuple[int, int]] = []
    cursor = start
    while cursor < end:
        window_end = min(end, cursor + MAX_EVIDENCE_SPAN_CHARS)
        if window_end < end:
            break_at = text_value.rfind(" ", cursor, window_end)
            if break_at > cursor + 120:
                window_end = break_at
        windows.append(trim_text_bounds(text_value, cursor, window_end))
        cursor = window_end
        while cursor < end and text_value[cursor].isspace():
            cursor += 1
    return windows


def trim_text_bounds(text_value: str, start: int, end: int) -> tuple[int, int]:
    while start < end and text_value[start].isspace():
        start += 1
    while end > start and text_value[end - 1].isspace():
        end -= 1
    return start, end


def score_evidence_text_unit(
    text_value: str,
    chunk: RetrievedChunkRead,
    role: str,
    plan: RetrievalPlan,
    *,
    metric_observations: list[MetricObservationRead] | None = None,
) -> tuple[float, list[str], str]:
    normalized = normalize_match_text(text_value)
    section_label = normalize_match_text(chunk.section_label)
    score = 0.0
    reasons: list[str] = []

    for metric_key in plan.metric_keys:
        metric_score, metric_reasons = metric_evidence_score(normalized, metric_key)
        score += metric_score
        reasons.extend(metric_reasons)

    if has_numeric_evidence(normalized):
        score += 0.12
        reasons.append("numeric_value")
    if has_comparison_language(normalized):
        score += 0.12
        reasons.append("comparison_language")
    if has_period_language(normalized):
        score += 0.06
        reasons.append("period_context")

    if role == "primary_financial_statement_chunks":
        value_match = match_selected_metric_observation(
            text_value,
            chunk,
            metric_observations or [],
        )
        if value_match.matched:
            score += 0.22 * value_match.confidence
            reasons.append("selected_fact_value")
            reasons.append(f"selected_fact_value_{value_match.kind}")
        if is_statement_context(normalized):
            score += 0.24
            reasons.append("statement_context")
        if chunk.has_table:
            score += 0.06
            reasons.append("table_context")
    elif role == "mda_explanation_chunks":
        if has_explanatory_language(normalized):
            score += 0.24
            reasons.append("explanatory_language")
        if is_mda_section_text(section_label):
            score += 0.05
            reasons.append("mda_section")
    elif role == "segment_or_product_breakdown_chunks":
        if has_business_breakdown_context(normalized):
            score += 0.22
            reasons.append("segment_or_product_context")
        if chunk.has_table:
            score += 0.05
            reasons.append("table_context")
    elif role == "risk_factor_chunks":
        if is_risk_section_text(section_label):
            score += 0.20
            reasons.append("risk_section")
        if has_risk_factor_language(normalized):
            score += 0.22
            reasons.append("risk_factor_language")
    elif role == "annual_context_chunks" and chunk.form_type == "10-K":
        score += 0.08
        reasons.append("annual_filing")

    if is_broad_comparison_noise_text(section_label, normalized):
        score -= 0.35
        reasons.append("noise_context")

    if not plan.metric_keys and role in plan.evidence_roles:
        score += 0.10
        reasons.append("planned_role")

    return max(0.0, min(score, 1.0)), _dedupe(reasons), infer_support_kind(role, reasons)


def metric_evidence_score(text_value: str, metric_key: str) -> tuple[float, list[str]]:
    profile = get_metric_profile(metric_key)
    if profile is None:
        generic_phrase = metric_key.replace("_", " ")
        return (0.16, [f"metric:{metric_key}"]) if generic_phrase in text_value else (0.0, [])

    score = 0.0
    reasons: list[str] = []
    if _contains_any(text_value, profile.strong_terms):
        score += 0.28
        reasons.append(f"strong_metric:{metric_key}")
    elif _contains_any(text_value, (*profile.weak_terms, *profile.aliases)):
        score += 0.14
        reasons.append(f"weak_metric:{metric_key}")

    if _contains_any(text_value, profile.statement_terms):
        score += 0.12
        reasons.append(f"statement_metric_context:{metric_key}")
    if _contains_any(text_value, profile.negative_terms):
        score -= 0.24
        reasons.append(f"negative_metric_context:{metric_key}")
    return score, reasons


def infer_support_kind(role: str, reasons: list[str]) -> str:
    if role == "primary_financial_statement_chunks":
        return "statement_value"
    if role == "mda_explanation_chunks" and "explanatory_language" in reasons:
        return "metric_driver"
    if role == "segment_or_product_breakdown_chunks":
        return "segment_breakdown"
    if role == "risk_factor_chunks":
        return "risk_factor"
    if role == "annual_context_chunks":
        return "annual_context"
    return "supporting_text"


def has_numeric_evidence(text_value: str) -> bool:
    return re.search(r"(?:[$€£]\s*)?\(?\d[\d,]*(?:\.\d+)?\)?%?", text_value) is not None


def matches_selected_metric_observation(
    text_value: str,
    chunk: RetrievedChunkRead,
    observations: list[MetricObservationRead],
) -> bool:
    return match_selected_metric_observation(text_value, chunk, observations).matched


def match_selected_metric_observation(
    text_value: str,
    chunk: RetrievedChunkRead,
    observations: list[MetricObservationRead],
) -> ValueMatch:
    if not observations:
        return ValueMatch(matched=False)
    for observation in observations:
        if observation.source_filing_id is not None and observation.source_filing_id != chunk.filing_id:
            component_match = match_metric_observation_components(text_value, chunk, observation)
            if component_match.matched:
                return component_match
            continue
        if (
            observation.source_filing_id is None
            and observation.source_accession_number
            and observation.source_accession_number != chunk.accession_number
        ):
            component_match = match_metric_observation_components(text_value, chunk, observation)
            if component_match.matched:
                return component_match
            continue
        value_match = match_metric_observation_value(text_value, observation.value)
        if value_match.matched:
            return value_match
        component_match = match_metric_observation_components(text_value, chunk, observation)
        if component_match.matched:
            return component_match
    return ValueMatch(matched=False)


def match_metric_observation_components(
    text_value: str,
    chunk: RetrievedChunkRead,
    observation: MetricObservationRead,
) -> ValueMatch:
    for component in observation.component_observations:
        if component.source_filing_id is not None and component.source_filing_id != chunk.filing_id:
            continue
        if (
            component.source_filing_id is None
            and component.source_accession_number
            and component.source_accession_number != chunk.accession_number
        ):
            continue
        value_match = match_metric_observation_value(text_value, component.value)
        if value_match.matched:
            return ValueMatch(
                matched=True,
                kind=f"component_{value_match.kind}",
                confidence=value_match.confidence * 0.88,
                matched_text=value_match.matched_text,
            )
    return ValueMatch(matched=False)


def metric_observation_value_in_text(text_value: str, value: Decimal) -> bool:
    return match_metric_observation_value(text_value, value).matched


def match_metric_observation_value(text_value: str, value: Decimal) -> ValueMatch:
    normalized_text = normalize_numeric_text(text_value)
    for candidate in metric_value_text_candidates(value):
        normalized_candidate = normalize_numeric_text(candidate.text)
        if len(normalized_candidate.lstrip("-")) < 3:
            continue
        match = re.search(
            rf"(?<!\d){re.escape(normalized_candidate)}(?!\d)",
            normalized_text,
        )
        if match is not None:
            return ValueMatch(
                matched=True,
                kind=candidate.kind,
                confidence=candidate.confidence,
                matched_text=match.group(0),
            )
    return ValueMatch(matched=False)


@dataclass(frozen=True)
class MetricValueCandidate:
    text: str
    kind: str
    confidence: float


def metric_value_text_candidates(value: Decimal) -> list[MetricValueCandidate]:
    scaled_values = [
        (value, "exact_raw", 1.0),
        (value / Decimal("1000"), "exact_thousands", 0.88),
        (value / Decimal("1000000"), "exact_millions", 0.96),
        (value / Decimal("1000000000"), "rounded_billions", 0.82),
    ]

    candidates: list[MetricValueCandidate] = []
    seen: set[str] = set()
    for candidate_value, kind, confidence in scaled_values:
        for text_variant in decimal_text_variants(candidate_value):
            normalized = normalize_numeric_text(text_variant)
            if normalized in seen:
                continue
            seen.add(normalized)
            candidates.append(
                MetricValueCandidate(
                    text=text_variant,
                    kind=kind,
                    confidence=confidence,
                )
            )
    return candidates


def decimal_text_variants(value: Decimal) -> list[str]:
    variants: list[str] = []
    abs_value = abs(value)
    sign = "-" if value < 0 else ""
    for places in (0, 1, 2, 3):
        quantizer = Decimal("1") if places == 0 else Decimal(f"1e-{places}")
        quantized = abs_value.quantize(quantizer)
        plain = f"{quantized:.{places}f}" if places else f"{quantized:.0f}"
        plain = plain.rstrip("0").rstrip(".") if "." in plain else plain
        variants.append(f"{sign}{plain}")
        try:
            variants.append(f"{sign}{quantized:,.{places}f}".rstrip("0").rstrip("."))
        except ValueError:
            pass
    return variants


def normalize_numeric_text(text_value: str) -> str:
    return (
        text_value.lower()
        .replace(",", "")
        .replace("$", "")
        .replace("(", "-")
        .replace(")", "")
        .replace(" ", "")
    )


def has_comparison_language(text_value: str) -> bool:
    return _contains_any(
        text_value,
        (
            "increased",
            "decreased",
            "higher",
            "lower",
            "compared to",
            "compared with",
            "year-over-year",
            "year over year",
            "versus",
            "vs.",
        ),
    )


def has_period_language(text_value: str) -> bool:
    return _contains_any(
        text_value,
        (
            "three months ended",
            "six months ended",
            "nine months ended",
            "year ended",
            "years ended",
            "quarter",
            "fiscal year",
            "202",
        ),
    )


def has_risk_factor_language(text_value: str) -> bool:
    return _contains_any(
        text_value,
        (
            "risk",
            "risks",
            "could adversely affect",
            "material adverse",
            "subject to",
            "uncertain",
            "may not",
            "could fail",
            "competition",
            "regulatory",
            "macroeconomic",
            "supply chain",
            "privacy",
            "cybersecurity",
            "litigation",
            "geopolitical",
            "tariff",
            "foreign exchange",
        ),
    )


def has_strong_risk_factor_signal(text_value: str) -> bool:
    return _contains_any(
        text_value,
        (
            "risk factors",
            "item 1a",
            "could adversely affect",
            "material adverse",
        ),
    )


def normalize_evidence_span_text(text_value: str) -> str:
    return " ".join(text_value.split())


def truncate_evidence_span_text(text_value: str) -> str:
    normalized = normalize_evidence_span_text(text_value)
    if len(normalized) <= MAX_EVIDENCE_SPAN_CHARS:
        return normalized
    return f"{normalized[: MAX_EVIDENCE_SPAN_CHARS - 1].rstrip()}..."


def evidence_span_text_key(text_value: str) -> str:
    return re.sub(r"\W+", " ", normalize_evidence_span_text(text_value).lower()).strip()


def evidence_spans_for_pack(pack: EvidencePackRead) -> list[EvidenceSpanRead]:
    return [
        *pack.primary_financial_statement_spans,
        *pack.mda_explanation_spans,
        *pack.segment_or_product_breakdown_spans,
        *pack.risk_factor_spans,
        *pack.annual_context_spans,
    ]


def build_final_evidence_pack(
    chunks: list[RetrievedChunkRead],
    metric_comparisons: list[MetricComparisonRead],
    plan: RetrievalPlan,
    *,
    metric_observations: list[MetricObservationRead] | None = None,
    chunk_text_by_id: dict[int, str] | None = None,
) -> tuple[EvidencePackRead, dict[str, Any]]:
    chunk_text_by_id = chunk_text_by_id or {}
    metric_observations = metric_observations or []
    comparison_limit = evidence_pack_comparison_limit(plan)
    chunk_quotas = dict(EVIDENCE_PACK_CHUNK_QUOTAS)
    if not should_include_annual_context(plan):
        chunk_quotas["annual_context_chunks"] = 0

    selected_chunk_ids: set[int] = set()
    selected_by_role: dict[str, list[RetrievedChunkRead]] = {
        role: [] for role in EVIDENCE_PACK_CHUNK_ROLE_ORDER
    }
    candidate_roles: dict[str, list[str]] = {
        role: [] for role in EVIDENCE_PACK_CHUNK_ROLE_ORDER
    }
    skipped: list[dict[str, str]] = []

    role_candidates: dict[str, list[tuple[float, int, int, RetrievedChunkRead]]] = {
        role: [] for role in EVIDENCE_PACK_CHUNK_ROLE_ORDER
    }
    span_candidates_by_chunk_role: dict[tuple[int, str], list[EvidenceSpanRead]] = {}
    span_candidate_trace: dict[str, list[dict[str, Any]]] = {
        role: [] for role in EVIDENCE_PACK_CHUNK_ROLE_ORDER
    }
    for rank, chunk in enumerate(chunks, start=1):
        roles = classify_evidence_roles(chunk, plan, chunk_text_by_id=chunk_text_by_id)
        for role in roles:
            if role not in role_candidates:
                continue
            spans = select_evidence_spans_for_chunk(
                chunk,
                role,
                plan,
                chunk_text_by_id=chunk_text_by_id,
                metric_observations=metric_observations,
            )
            span_candidates_by_chunk_role[(chunk.chunk_id, role)] = spans
            span_candidate_trace[role].extend(
                {
                    "evidence_id": span.evidence_id,
                    "chunk_id": span.chunk_id,
                    "score": span.score,
                    "support_kind": span.support_kind,
                    "reasons": span.reasons,
                }
                for span in spans
            )
            role_score = evidence_role_score(
                chunk,
                role,
                chunk_text_by_id=chunk_text_by_id,
            )
            if spans:
                role_score += min(spans[0].score, 1.0) * 0.08
            role_candidates[role].append(
                (
                    role_score,
                    -rank,
                    chunk.chunk_id,
                    chunk,
                )
            )
            candidate_roles[role].append(chunk.evidence_id)

    for role in EVIDENCE_PACK_CHUNK_ROLE_ORDER:
        quota = chunk_quotas[role]
        ordered_candidates = sorted(role_candidates[role], reverse=True)
        if quota <= 0:
            for _, _, _, chunk in ordered_candidates:
                skipped.append(
                    {
                        "evidence_id": chunk.evidence_id,
                        "role": role,
                        "reason": "role_quota_zero",
                    }
                )
            continue

        selected_for_role: set[int] = set()
        for _, _, _, chunk in ordered_candidates:
            if len(selected_by_role[role]) >= quota:
                break
            if chunk.chunk_id in selected_chunk_ids:
                continue
            selected_by_role[role].append(chunk)
            selected_for_role.add(chunk.chunk_id)
            selected_chunk_ids.add(chunk.chunk_id)

        if len(selected_by_role[role]) < quota:
            for _, _, _, chunk in ordered_candidates:
                if len(selected_by_role[role]) >= quota:
                    break
                if chunk.chunk_id in selected_for_role:
                    continue
                if chunk.chunk_id not in selected_chunk_ids:
                    continue
                if not span_candidates_by_chunk_role.get((chunk.chunk_id, role)):
                    continue
                selected_by_role[role].append(chunk)
                selected_for_role.add(chunk.chunk_id)

        for _, _, _, chunk in ordered_candidates:
            if chunk.chunk_id in selected_for_role:
                continue
            reason = (
                "role_quota_full"
                if len(selected_by_role[role]) >= quota
                else "already_selected_for_higher_priority_role"
                if chunk.chunk_id in selected_chunk_ids
                else "not_selected"
            )
            skipped.append(
                {
                    "evidence_id": chunk.evidence_id,
                    "role": role,
                    "reason": reason,
                }
            )

    if plan.metric_keys and not selected_by_role["primary_financial_statement_chunks"]:
        fallback = best_primary_statement_fallback(
            chunks,
            chunk_text_by_id=chunk_text_by_id,
        )
        if fallback is not None:
            selected_by_role["annual_context_chunks"] = [
                chunk
                for chunk in selected_by_role["annual_context_chunks"]
                if chunk.chunk_id != fallback.chunk_id
            ]
            if all(
                chunk.chunk_id != fallback.chunk_id
                for chunk in selected_by_role["primary_financial_statement_chunks"]
            ):
                selected_by_role["primary_financial_statement_chunks"].append(fallback)
            selected_chunk_ids.add(fallback.chunk_id)

    selected_spans_by_role, selected_span_trace = build_selected_evidence_spans(
        selected_by_role,
        span_candidates_by_chunk_role,
        plan,
        chunk_text_by_id=chunk_text_by_id,
        metric_observations=metric_observations,
    )
    selected_comparisons = metric_comparisons[:comparison_limit]
    pack = EvidencePackRead(
        metric_observations=metric_observations,
        metric_comparisons=selected_comparisons,
        primary_financial_statement_chunks=selected_by_role[
            "primary_financial_statement_chunks"
        ],
        mda_explanation_chunks=selected_by_role["mda_explanation_chunks"],
        segment_or_product_breakdown_chunks=selected_by_role[
            "segment_or_product_breakdown_chunks"
        ],
        risk_factor_chunks=selected_by_role["risk_factor_chunks"],
        annual_context_chunks=selected_by_role["annual_context_chunks"],
        primary_financial_statement_spans=selected_spans_by_role[
            "primary_financial_statement_chunks"
        ],
        mda_explanation_spans=selected_spans_by_role["mda_explanation_chunks"],
        segment_or_product_breakdown_spans=selected_spans_by_role[
            "segment_or_product_breakdown_chunks"
        ],
        risk_factor_spans=selected_spans_by_role["risk_factor_chunks"],
        annual_context_spans=selected_spans_by_role["annual_context_chunks"],
    )
    trace = {
        "comparison_limit": comparison_limit,
        "chunk_quotas": chunk_quotas,
        "span_quotas": EVIDENCE_PACK_SPAN_QUOTAS,
        "candidate_roles": candidate_roles,
        "span_candidates": span_candidate_trace,
        "selected": {
            "metric_comparisons": [
                comparison.evidence_id for comparison in selected_comparisons
            ],
            "metric_observations": [
                observation.evidence_id for observation in metric_observations
            ],
            **{
                role: [chunk.evidence_id for chunk in selected_chunks]
                for role, selected_chunks in selected_by_role.items()
            },
        },
        "selected_spans": {
            role: [span.evidence_id for span in selected_spans]
            for role, selected_spans in selected_spans_by_role.items()
        },
        "span_skipped": selected_span_trace["skipped"],
        "skipped": skipped,
    }
    return pack, trace


def best_primary_statement_fallback(
    chunks: list[RetrievedChunkRead],
    *,
    chunk_text_by_id: dict[int, str] | None = None,
) -> RetrievedChunkRead | None:
    statement_chunks = [
        chunk
        for chunk in chunks
        if is_primary_financial_statement_chunk(
            chunk,
            chunk_text_by_id=chunk_text_by_id,
        )
    ]
    if not statement_chunks:
        return None
    return max(
        statement_chunks,
        key=lambda chunk: evidence_role_score(
            chunk,
            "primary_financial_statement_chunks",
            chunk_text_by_id=chunk_text_by_id,
        ),
    )


def evidence_candidate_limit(plan: RetrievalPlan, *, top_k: int) -> int:
    if plan.question_type == "broad_comparison":
        return max(top_k, 30)
    if (
        plan.metric_keys
        and plan.comparison_basis != "none"
        and "Management's Discussion and Analysis" in plan.target_sections
    ):
        return max(top_k, 20)
    return top_k


def evidence_pack_comparison_limit(plan: RetrievalPlan) -> int:
    if not should_build_metric_comparisons(plan):
        return 0
    if len(plan.comparison_candidates) > 1 or plan.comparison_basis == "ambiguous":
        return min(6, max(2, len(plan.metric_keys) * 2))
    if len(plan.metric_keys) > 1:
        return min(8, max(2, len(plan.metric_keys)))
    return 1


def should_include_annual_context(plan: RetrievalPlan) -> bool:
    comparison_basis = plan.default_comparison_basis or plan.comparison_basis
    if plan.comparison_basis == "ambiguous" and "latest_fy_yoy" in plan.comparison_candidates:
        return True
    if comparison_basis in {"latest_fy_yoy", "previous_fy_yoy"}:
        return False
    return plan.time_scope in {"comparison_trend", "annual"}


def should_warn_empty_evidence_pack(
    plan: RetrievalPlan,
    pack: EvidencePackRead,
) -> bool:
    expects_role_evidence = bool(
        plan.metric_keys
        or set(plan.evidence_roles).intersection(EVIDENCE_PACK_CHUNK_QUOTAS)
        or set(plan.target_sections).intersection(
            {
                "Financial Statements",
                "Management's Discussion and Analysis",
                "Risk Factors",
                "Liquidity",
                "Cash Flows",
            }
        )
    )
    if not expects_role_evidence:
        return False
    return not any(
        (
            pack.metric_observations,
            pack.metric_comparisons,
            pack.primary_financial_statement_chunks,
            pack.mda_explanation_chunks,
            pack.segment_or_product_breakdown_chunks,
            pack.risk_factor_chunks,
            pack.annual_context_chunks,
        )
    )


def classify_evidence_roles(
    chunk: RetrievedChunkRead,
    plan: RetrievalPlan,
    *,
    chunk_text_by_id: dict[int, str] | None = None,
) -> list[str]:
    candidate_roles = evidence_pack_candidate_roles(plan)
    roles: list[str] = []
    annual_context = is_annual_context_chunk(
        chunk,
        plan,
        chunk_text_by_id=chunk_text_by_id,
    )
    primary_statement = is_primary_financial_statement_chunk(
        chunk,
        chunk_text_by_id=chunk_text_by_id,
    )
    mda = is_mda_chunk(chunk)
    segment_breakdown = is_segment_or_product_breakdown_chunk(
        chunk,
        chunk_text_by_id=chunk_text_by_id,
    )
    risk_factor = is_risk_factor_chunk(chunk, chunk_text_by_id=chunk_text_by_id)

    if (
        primary_statement
        and not annual_context
        and "primary_financial_statement_chunks" in candidate_roles
    ):
        roles.append("primary_financial_statement_chunks")
    if mda and "mda_explanation_chunks" in candidate_roles:
        roles.append("mda_explanation_chunks")
    if segment_breakdown and "segment_or_product_breakdown_chunks" in candidate_roles:
        roles.append("segment_or_product_breakdown_chunks")
    if risk_factor and "risk_factor_chunks" in candidate_roles:
        roles.append("risk_factor_chunks")
    if annual_context and "annual_context_chunks" in candidate_roles:
        roles.append("annual_context_chunks")
    return roles


def evidence_pack_candidate_roles(plan: RetrievalPlan) -> set[str]:
    roles = {
        role
        for role in plan.evidence_roles
        if role in EVIDENCE_PACK_CHUNK_QUOTAS
    }
    if not roles:
        if plan.metric_keys:
            roles.update(
                {
                    "primary_financial_statement_chunks",
                    "mda_explanation_chunks",
                    "segment_or_product_breakdown_chunks",
                }
            )
        if (
            "Financial Statements" in plan.target_sections
            or "Cash Flows" in plan.target_sections
        ):
            roles.add("primary_financial_statement_chunks")
        if "Management's Discussion and Analysis" in plan.target_sections:
            roles.add("mda_explanation_chunks")
        if plan.question_type in {"broad_comparison", "performance_overview"}:
            roles.add("segment_or_product_breakdown_chunks")
        if "Risk Factors" in plan.target_sections or plan.question_type == "risk":
            roles.add("risk_factor_chunks")

    if should_include_annual_context(plan):
        roles.add("annual_context_chunks")
    return roles


def evidence_role_score(
    chunk: RetrievedChunkRead,
    role: str,
    *,
    chunk_text_by_id: dict[int, str] | None = None,
) -> float:
    text_value = normalized_evidence_text(chunk, chunk_text_by_id=chunk_text_by_id)
    score = chunk.score
    if role == "primary_financial_statement_chunks" and is_statement_context(text_value):
        score += 0.08
    if role == "mda_explanation_chunks":
        if has_explanatory_language(text_value):
            score += 0.08
        if not chunk.has_table:
            score += 0.02
    if role == "segment_or_product_breakdown_chunks":
        if is_mda_chunk(chunk):
            score += 0.04
        if chunk.has_table:
            score += 0.02
    if role == "risk_factor_chunks":
        if is_risk_factor_chunk(chunk, chunk_text_by_id=chunk_text_by_id):
            score += 0.08
        if has_risk_factor_language(text_value):
            score += 0.05
    if role == "annual_context_chunks" and chunk.form_type == "10-K":
        score += 0.04
    return score


def is_primary_financial_statement_chunk(
    chunk: RetrievedChunkRead,
    *,
    chunk_text_by_id: dict[int, str] | None = None,
) -> bool:
    text_value = normalized_evidence_text(chunk, chunk_text_by_id=chunk_text_by_id)
    return is_financial_statement_section(chunk) and is_statement_context(text_value)


def is_annual_context_chunk(
    chunk: RetrievedChunkRead,
    plan: RetrievalPlan,
    *,
    chunk_text_by_id: dict[int, str] | None = None,
) -> bool:
    if chunk.form_type != "10-K":
        return False
    if not is_financial_statement_section(chunk) and not is_mda_chunk(chunk):
        return False
    comparison_basis = plan.default_comparison_basis or plan.comparison_basis
    if comparison_basis == "latest_fy_yoy" and plan.comparison_basis != "ambiguous":
        return False
    return comparison_basis in {
        "latest_quarter_yoy",
        "previous_quarter_yoy",
        "latest_ytd_yoy",
        "previous_ytd_yoy",
    } or (
        "latest_fy_yoy" in plan.comparison_candidates
    )


def is_financial_statement_section(chunk: RetrievedChunkRead) -> bool:
    section_label = chunk.section_label.lower()
    return (
        "financial statements" in section_label
        or "item 8" in section_label
        or "item 1 - financial" in section_label
    )


def is_mda_chunk(chunk: RetrievedChunkRead) -> bool:
    section_label = chunk.section_label.lower()
    return (
        "management" in section_label
        or "discussion and analysis" in section_label
        or "md&a" in section_label
        or "item 2" in section_label
    )


def is_risk_factor_chunk(
    chunk: RetrievedChunkRead,
    *,
    chunk_text_by_id: dict[int, str] | None = None,
) -> bool:
    section_label = normalize_match_text(chunk.section_label)
    text_value = normalized_evidence_text(chunk, chunk_text_by_id=chunk_text_by_id)
    return is_risk_section_text(section_label) or (
        has_risk_factor_language(text_value)
        and has_strong_risk_factor_signal(text_value)
    )


def is_risk_section_text(section_label: str) -> bool:
    return "risk factors" in section_label or "item 1a" in section_label


def is_statement_context(text_value: str) -> bool:
    return _contains_any(
        text_value,
        (
            "statements of operations",
            "statement of operations",
            "condensed consolidated statements",
            "consolidated statements",
            "total net sales",
            "total revenue",
        ),
    )


def is_segment_or_product_breakdown_chunk(
    chunk: RetrievedChunkRead,
    *,
    chunk_text_by_id: dict[int, str] | None = None,
) -> bool:
    text_value = normalized_evidence_text(chunk, chunk_text_by_id=chunk_text_by_id)
    return _contains_any(
        text_value,
        (
            "segment information",
            "segment operating",
            "reportable segment",
            "products and services performance",
            "by category",
            "americas",
            "greater china",
            "rest of asia pacific",
            "iphone",
            "ipad",
            "mac",
            "services",
            "geographic",
        ),
    )


def has_explanatory_language(text_value: str) -> bool:
    return _contains_any(
        text_value,
        (
            "due to",
            "primarily due to",
            "because",
            "driven by",
            "attributable to",
            "resulted from",
            "higher net sales",
            "lower net sales",
            "increased during",
            "decreased during",
            "compared to",
            "year-over-year",
        ),
    )


def normalized_evidence_text(
    chunk: RetrievedChunkRead,
    *,
    chunk_text_by_id: dict[int, str] | None = None,
) -> str:
    text_value = (
        chunk_text_by_id.get(chunk.chunk_id, chunk.snippet)
        if chunk_text_by_id
        else chunk.snippet
    )
    return f"{chunk.section_label}\n{text_value}".lower()


def find_comparison_pair(
    facts: list[FinancialFact],
    basis: str,
) -> tuple[FinancialFact, FinancialFact] | None:
    basis_config = {
        "latest_quarter_yoy": ("quarter", 0),
        "previous_quarter_yoy": ("quarter", 1),
        "latest_ytd_yoy": ("ytd", 0),
        "previous_ytd_yoy": ("ytd", 1),
        "latest_fy_yoy": ("fy", 0),
        "previous_fy_yoy": ("fy", 1),
    }.get(basis)
    if basis_config is None:
        return None
    duration_class, pair_offset = basis_config

    classified = [
        fact
        for fact in facts
        if classify_fact_duration(fact) == duration_class
    ]
    classified.sort(
        key=lambda fact: (
            fact.period_end,
            fact.filed_date or date.min,
            fact.id or 0,
        ),
        reverse=True,
    )
    comparable_pairs: list[tuple[FinancialFact, FinancialFact]] = []
    for current in classified:
        prior = find_prior_comparable_fact(current, classified)
        if prior is not None:
            comparable_pairs.append((current, prior))
            if len(comparable_pairs) > pair_offset:
                return comparable_pairs[pair_offset]
    return None


def find_prior_comparable_fact(
    current: FinancialFact,
    candidates: list[FinancialFact],
) -> FinancialFact | None:
    exact_matches = [
        candidate
        for candidate in candidates
        if candidate.id != current.id
        and candidate.period_end < current.period_end
        and candidate.fiscal_period == current.fiscal_period
    ]
    if exact_matches:
        return max(exact_matches, key=lambda fact: (fact.period_end, fact.id or 0))

    window_matches = [
        candidate
        for candidate in candidates
        if candidate.id != current.id
        and candidate.period_end < current.period_end
        and 300 <= (current.period_end - candidate.period_end).days <= 430
    ]
    if window_matches:
        return max(window_matches, key=lambda fact: (fact.period_end, fact.id or 0))
    return None


def classify_fact_duration(fact: FinancialFact) -> str | None:
    if fact.period_start is None:
        return "fy" if fact.fiscal_period == "FY" else "instant"

    duration_days = (fact.period_end - fact.period_start).days + 1
    if fact.fiscal_period == "FY" or duration_days >= 300:
        return "fy"
    if duration_days <= 115:
        return "quarter"
    if duration_days <= 285:
        return "ytd"
    return None


def build_metric_comparison(
    metric_key: str,
    basis: str,
    current: FinancialFact,
    prior: FinancialFact,
) -> MetricComparisonRead:
    current_value = Decimal(current.value)
    prior_value = Decimal(prior.value)
    current_duration_class = classify_fact_duration(current)
    prior_duration_class = classify_fact_duration(prior)
    growth_rate = None
    if prior_value != 0:
        growth_rate = (current_value - prior_value) / abs(prior_value)

    return MetricComparisonRead(
        evidence_id=f"metric_comparison:{metric_key}:{basis}:{current.id}:{prior.id}",
        basis=basis,
        canonical_metric_key=metric_key,
        current_fact_id=current.id,
        prior_fact_id=prior.id,
        current_period_start=current.period_start,
        current_period_end=current.period_end,
        prior_period_start=prior.period_start,
        prior_period_end=prior.period_end,
        current_duration_class=current_duration_class,
        prior_duration_class=prior_duration_class,
        current_period_label=format_fact_period_label(current, current_duration_class),
        prior_period_label=format_fact_period_label(prior, prior_duration_class),
        current_value=current_value,
        prior_value=prior_value,
        growth_rate=growth_rate,
        current_source_fiscal_year=current.source_fiscal_year,
        current_fact_fiscal_year=current.fact_fiscal_year,
        prior_source_fiscal_year=prior.source_fiscal_year,
        prior_fact_fiscal_year=prior.fact_fiscal_year,
        current_fiscal_period=current.fiscal_period,
        prior_fiscal_period=prior.fiscal_period,
        current_source_filing_url=current.source_filing_url,
        prior_source_filing_url=prior.source_filing_url,
    )


def build_source_coverage_summary(
    chunks: list[RetrievedChunkRead],
    facts: list[RetrievedFinancialFactRead],
    metric_comparisons: list[MetricComparisonRead] | None = None,
    evidence_pack: EvidencePackRead | None = None,
) -> dict[str, Any]:
    metric_comparisons = metric_comparisons or []
    filing_dates = [chunk.filing_date for chunk in chunks]
    return {
        "chunk_count": len(chunks),
        "fact_count": len(facts),
        "metric_comparison_count": len(metric_comparisons),
        "metric_observation_count": (
            len(evidence_pack.metric_observations) if evidence_pack is not None else 0
        ),
        "evidence_span_count": (
            len(evidence_spans_for_pack(evidence_pack)) if evidence_pack is not None else 0
        ),
        "forms": sorted({chunk.form_type for chunk in chunks}),
        "sections": sorted({chunk.section_label for chunk in chunks}),
        "latest_chunk_filing_date": max(filing_dates).isoformat() if filing_dates else None,
        "fact_metric_keys": sorted({fact.canonical_metric_key for fact in facts}),
        "comparison_bases": sorted({comparison.basis for comparison in metric_comparisons}),
    }


def build_chunk_filter_sql(
    request: RetrievalRequest,
    params: dict[str, Any],
    *,
    table_alias: str = "dc",
    plan: RetrievalPlan | None = None,
    retrieval_scope: RetrievalScope | None = None,
    latest_filing_date: date | None = None,
) -> str:
    clauses: list[str] = []
    form_types = effective_form_types(request, plan)
    if len(form_types) == 1:
        params["filter_form_type"] = form_types[0]
        clauses.append(f"AND {table_alias}.form_type = :filter_form_type")
    elif len(form_types) > 1:
        placeholders: list[str] = []
        for index, form_type in enumerate(form_types):
            param_name = f"filter_form_type_{index}"
            params[param_name] = form_type
            placeholders.append(f":{param_name}")
        clauses.append(f"AND {table_alias}.form_type IN ({', '.join(placeholders)})")
    if request.date_from is not None:
        params["date_from"] = request.date_from
        clauses.append(f"AND {table_alias}.filing_date >= :date_from")
    if request.date_to is not None:
        params["date_to"] = request.date_to
        clauses.append(f"AND {table_alias}.filing_date <= :date_to")
    if retrieval_scope is not None and retrieval_scope.filing_ids:
        placeholders: list[str] = []
        for index, filing_id in enumerate(retrieval_scope.filing_ids):
            param_name = f"filter_filing_id_{index}"
            params[param_name] = filing_id
            placeholders.append(f":{param_name}")
        clauses.append(f"AND {table_alias}.filing_id IN ({', '.join(placeholders)})")
    elif retrieval_scope is not None and retrieval_scope.accession_numbers:
        placeholders = []
        for index, accession_number in enumerate(retrieval_scope.accession_numbers):
            param_name = f"filter_accession_number_{index}"
            params[param_name] = accession_number
            placeholders.append(f":{param_name}")
        clauses.append(
            f"AND {table_alias}.accession_number IN ({', '.join(placeholders)})"
        )
    elif latest_filing_date is not None:
        params["latest_filing_date"] = latest_filing_date
        clauses.append(f"AND {table_alias}.filing_date = :latest_filing_date")
    if request.section is not None and request.section.strip():
        params["section_like"] = f"%{request.section.strip()}%"
        clauses.append(f"AND {table_alias}.section_label ILIKE :section_like")
    return "\n".join(clauses)


def effective_form_types(
    request: RetrievalRequest,
    plan: RetrievalPlan | None = None,
) -> list[str]:
    if request.form_type is not None and request.form_type.strip():
        return [request.form_type.strip().upper()]
    if plan is None:
        return []
    allowed_forms = getattr(plan, "allowed_forms", [])
    if allowed_forms:
        return [
            form_type
            for form_type in dict.fromkeys(form.strip().upper() for form in allowed_forms)
            if form_type
        ]
    return [
        form_type
        for form_type in dict.fromkeys(form.strip().upper() for form in plan.forms)
        if form_type
    ]


def effective_duration_class(
    plan: RetrievalPlan,
    retrieval_scope: RetrievalScope | None = None,
) -> str | None:
    if retrieval_scope is not None and retrieval_scope.duration_class:
        return retrieval_scope.duration_class
    duration_class = getattr(plan, "duration_class", None)
    if duration_class in {"quarter", "ytd", "fy", "instant"}:
        return duration_class
    basis = plan.default_comparison_basis or plan.comparison_basis
    if basis in {"latest_quarter_yoy", "previous_quarter_yoy"}:
        return "quarter"
    if basis in {"latest_ytd_yoy", "previous_ytd_yoy"}:
        return "ytd"
    if basis in {"latest_fy_yoy", "previous_fy_yoy"}:
        return "fy"
    return None


def form_priority_sort_expression(plan: RetrievalPlan):
    preferred_forms = getattr(plan, "preferred_forms", []) or []
    whens = {
        form_type: index
        for index, form_type in enumerate(preferred_forms)
    }
    if not whens:
        return Filing.id.desc()
    return case(
        whens,
        value=Filing.form_type,
        else_=len(whens),
    ).asc()


def negative_computed_fact_id(*facts: FinancialFact) -> int:
    seed = 0
    for fact in facts:
        seed = seed * 1_000_003 + int(fact.id or 0)
    return -abs(seed or 1)


def latest_filing_scope_reason(plan: RetrievalPlan) -> str | None:
    if plan.time_scope == "latest":
        return "time_scope:latest"

    if (
        plan.comparison_basis in LATEST_FILING_COMPARISON_BASES
        and plan.comparison_candidates == [plan.comparison_basis]
    ):
        return f"comparison_basis:{plan.comparison_basis}"

    return None


def should_build_metric_comparisons(plan: RetrievalPlan) -> bool:
    if not plan.needs_metric_comparisons:
        return False
    if not plan.metric_keys:
        return False
    if plan.comparison_candidates:
        return True
    return plan.comparison_basis not in {"none", "ambiguous"}


def form_priority_boost(chunk: DocumentChunk, plan: RetrievalPlan) -> float:
    comparison_basis = plan.default_comparison_basis or plan.comparison_basis
    if comparison_basis in {
        "latest_quarter_yoy",
        "previous_quarter_yoy",
        "latest_ytd_yoy",
        "previous_ytd_yoy",
    }:
        return 0.07 if chunk.form_type == "10-Q" else 0.02
    if comparison_basis in {"latest_fy_yoy", "previous_fy_yoy"}:
        return 0.07 if chunk.form_type == "10-K" else 0.02
    return 0.05 if chunk.form_type == "10-K" else 0.04


def format_fact_period_label(fact: FinancialFact, duration_class: str | None = None) -> str:
    duration = duration_class or classify_fact_duration(fact)
    fiscal_year = fact.fact_fiscal_year or fact.source_fiscal_year or fact.period_end.year
    if duration == "quarter":
        return f"{fact.fiscal_period or 'Quarter'} {fiscal_year} quarter"
    if duration == "ytd":
        return f"{fact.fiscal_period or 'YTD'} {fiscal_year} year-to-date"
    if duration == "fy":
        return f"FY {fiscal_year}"
    if duration == "instant":
        return f"As of {fact.period_end.isoformat()}"
    return f"Period ended {fact.period_end.isoformat()}"


def make_snippet(
    text_value: str,
    *,
    max_chars: int = 500,
    metric_keys: list[str] | None = None,
) -> str:
    normalized = " ".join(text_value.split())
    if len(normalized) <= max_chars:
        return normalized
    hit_index = find_metric_hit_index(normalized, metric_keys or [])
    if hit_index is not None and hit_index > 120:
        start = max(0, hit_index - 120)
        end = start + max_chars - 4
        return f"...{normalized[start:end].strip()}..."
    return f"{normalized[: max_chars - 1].rstrip()}..."


def find_metric_hit_index(text_value: str, metric_keys: list[str]) -> int | None:
    lower_text = text_value.lower()
    indices: list[int] = []
    for metric_key in metric_keys:
        profile = get_metric_profile(metric_key)
        terms = (
            (*profile.strong_terms, *profile.weak_terms)
            if profile is not None
            else (metric_key.replace("_", " "),)
        )
        for term in terms:
            index = lower_text.find(term)
            if index >= 0:
                indices.append(index)
    return min(indices) if indices else None


def vector_literal(values: list[float]) -> str:
    return "[" + ",".join(str(float(value)) for value in values) + "]"


def _contains_any(text_value: str, terms: tuple[str, ...]) -> bool:
    return any(term in text_value for term in terms)


def _dedupe(values: list[str]) -> list[str]:
    return list(dict.fromkeys(values))


def _elapsed_ms(started: float) -> float:
    return round((perf_counter() - started) * 1000, 3)

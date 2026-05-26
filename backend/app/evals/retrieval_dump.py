from __future__ import annotations

import argparse
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
import json
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_sessionmaker
from app.models import DocumentChunk
from app.schemas import RetrievalRequest, RetrievalResponse
from app.schemas.retrieval import (
    EvidencePackRead,
    RetrievedChunkRead,
    RetrievedFinancialFactRead,
)
from app.services import RetrievalService


DEFAULT_MAX_CHARS = 1800


@dataclass(frozen=True)
class RetrievalDump:
    ticker: str
    question: str
    response: RetrievalResponse
    chunk_text_by_id: dict[int, str]
    generated_at: datetime


def build_retrieval_dump(
    db: Session,
    *,
    ticker: str,
    question: str,
    max_chunks: int | None = None,
) -> RetrievalDump:
    response = RetrievalService(db).retrieve(
        RetrievalRequest(ticker=ticker, question=question)
    )
    if max_chunks is not None:
        response.retrieved_chunks = response.retrieved_chunks[:max_chunks]

    chunk_ids = [chunk.chunk_id for chunk in response.retrieved_chunks]
    chunk_text_by_id = load_chunk_texts(db, chunk_ids)
    return RetrievalDump(
        ticker=ticker,
        question=question,
        response=response,
        chunk_text_by_id=chunk_text_by_id,
        generated_at=datetime.now(UTC),
    )


def load_chunk_texts(db: Session, chunk_ids: list[int]) -> dict[int, str]:
    if not chunk_ids:
        return {}
    statement = select(DocumentChunk.id, DocumentChunk.chunk_text).where(
        DocumentChunk.id.in_(chunk_ids)
    )
    return {
        int(chunk_id): str(chunk_text)
        for chunk_id, chunk_text in db.execute(statement).all()
    }


def format_retrieval_dump_markdown(
    dump: RetrievalDump,
    *,
    max_chars: int = DEFAULT_MAX_CHARS,
    include_full_text: bool = False,
) -> str:
    response = dump.response
    trace = response.retrieval_trace
    lines = [
        "# Retrieval Evaluation Dump",
        "",
        "## Query",
        "",
        f"- ticker: `{dump.ticker}`",
        f"- question: {dump.question}",
        f"- generated_at_utc: `{dump.generated_at.isoformat()}`",
        "",
        "## Suggested Judge Instructions",
        "",
        "Rate each chunk from 0 to 3:",
        "",
        "- 3 = directly answers the question with core evidence",
        "- 2 = useful supporting context",
        "- 1 = superficially related but not enough to support an answer",
        "- 0 = wrong metric, period, section, company, or noise",
        "",
        "Then answer:",
        "",
        "- Are the top chunks sufficient to answer the question?",
        "- Is the evidence pack missing any required role, metric, period, or source?",
        "- Which chunk ids should be treated as gold evidence for this query?",
        "",
        "## Retrieval Plan",
        "",
        _json_block(response.retrieval_plan),
        "",
        "## Source Coverage",
        "",
        _json_block(response.source_coverage_summary),
        "",
        "## Candidate Counts",
        "",
        _json_block(trace.get("candidate_counts", {})),
        "",
        "## Chunk Scope",
        "",
        _json_block(trace.get("chunk_scope", {})),
        "",
    ]

    dense_sources = trace.get("dense_query_sources")
    if dense_sources:
        lines.extend(
            [
                "## Dense Query Sources",
                "",
                _json_block(dense_sources),
                "",
            ]
        )

    degraded = trace.get("degraded", [])
    if degraded:
        lines.extend(
            [
                "## Degraded Warnings",
                "",
                _json_block(degraded),
                "",
            ]
        )

    lines.extend(format_metric_comparisons(response))
    lines.extend(format_evidence_pack(response.final_evidence_pack))
    lines.extend(
        format_top_chunks(
            response.retrieved_chunks,
            dump.chunk_text_by_id,
            max_chars=max_chars,
            include_full_text=include_full_text,
        )
    )
    lines.extend(format_top_facts(response.retrieved_facts))
    return "\n".join(lines).rstrip() + "\n"


def format_metric_comparisons(response: RetrievalResponse) -> list[str]:
    lines = [
        "## Metric Comparisons",
        "",
    ]
    if not response.metric_comparisons:
        lines.extend(["No metric comparisons returned.", ""])
        return lines

    for index, comparison in enumerate(response.metric_comparisons, start=1):
        lines.extend(
            [
                f"### Comparison {index}: {comparison.canonical_metric_key}",
                "",
                f"- evidence_id: `{comparison.evidence_id}`",
                f"- basis: `{comparison.basis}`",
                f"- current: {comparison.current_period_label or comparison.current_period_end} = {comparison.current_value}",
                f"- prior: {comparison.prior_period_label or comparison.prior_period_end} = {comparison.prior_value}",
                f"- growth_rate: {comparison.growth_rate}",
                "",
            ]
        )
    return lines


def format_evidence_pack(pack: EvidencePackRead) -> list[str]:
    role_chunks = {
        "primary_financial_statement_chunks": pack.primary_financial_statement_chunks,
        "mda_explanation_chunks": pack.mda_explanation_chunks,
        "segment_or_product_breakdown_chunks": pack.segment_or_product_breakdown_chunks,
        "annual_context_chunks": pack.annual_context_chunks,
    }
    role_spans = {
        "primary_financial_statement_spans": pack.primary_financial_statement_spans,
        "mda_explanation_spans": pack.mda_explanation_spans,
        "segment_or_product_breakdown_spans": pack.segment_or_product_breakdown_spans,
        "annual_context_spans": pack.annual_context_spans,
    }
    lines = [
        "## Final Evidence Pack",
        "",
        f"- metric_comparisons: {len(pack.metric_comparisons)}",
    ]
    for role, chunks in role_chunks.items():
        evidence_ids = ", ".join(chunk.evidence_id for chunk in chunks) or "none"
        lines.append(f"- {role}: {evidence_ids}")
    for role, spans in role_spans.items():
        evidence_ids = ", ".join(span.evidence_id for span in spans) or "none"
        lines.append(f"- {role}: {evidence_ids}")
    lines.append("")
    return lines


def format_top_chunks(
    chunks: list[RetrievedChunkRead],
    chunk_text_by_id: dict[int, str],
    *,
    max_chars: int,
    include_full_text: bool,
) -> list[str]:
    lines = [
        "## Top Retrieved Chunks",
        "",
    ]
    if not chunks:
        lines.extend(["No chunks returned.", ""])
        return lines

    for rank, chunk in enumerate(chunks, start=1):
        full_text = chunk_text_by_id.get(chunk.chunk_id, chunk.snippet)
        text_for_judge = full_text if include_full_text else truncate_text(full_text, max_chars)
        lines.extend(
            [
                f"### Rank {rank}: chunk:{chunk.chunk_id}",
                "",
                f"- evidence_id: `{chunk.evidence_id}`",
                f"- highlighted_source: `{highlighted_source_url(chunk)}`",
                f"- sec_source: `{chunk.sec_url}`",
                f"- score: {chunk.score}",
                f"- fusion_score: {chunk.fusion_score}",
                f"- source_ranks: `{json.dumps(chunk.source_ranks, ensure_ascii=False)}`",
                f"- rerank_boosts: `{json.dumps(chunk.rerank_boosts, ensure_ascii=False)}`",
                f"- form_type: `{chunk.form_type}`",
                f"- filing_date: `{chunk.filing_date}`",
                f"- section: {chunk.section_label}",
                f"- pages: {format_pages(chunk.start_page, chunk.end_page)}",
                "",
                "```text",
                text_for_judge,
                "```",
                "",
            ]
        )
    return lines


def format_top_facts(facts: list[RetrievedFinancialFactRead]) -> list[str]:
    lines = [
        "## Top Financial Facts",
        "",
    ]
    if not facts:
        lines.extend(["No financial facts returned.", ""])
        return lines

    for rank, fact in enumerate(facts, start=1):
        lines.extend(
            [
                f"### Fact {rank}: {fact.canonical_metric_key}",
                "",
                f"- evidence_id: `{fact.evidence_id}`",
                f"- label: {fact.label}",
                f"- period: {fact.period_label or fact.period_end}",
                f"- value: {fact.value} {fact.unit}",
                f"- form_type: `{fact.form_type}`",
                f"- source: `{fact.source_filing_url}`",
                "",
            ]
        )
    return lines


def retrieval_dump_to_jsonable(dump: RetrievalDump) -> dict[str, Any]:
    response = dump.response.model_dump(mode="json")
    response["chunk_text_by_id"] = {
        str(chunk_id): text
        for chunk_id, text in dump.chunk_text_by_id.items()
    }
    return {
        "ticker": dump.ticker,
        "question": dump.question,
        "generated_at_utc": dump.generated_at.isoformat(),
        "response": response,
    }


def highlighted_source_url(chunk: RetrievedChunkRead) -> str:
    return f"/filings/{chunk.filing_id}/chunks/{chunk.chunk_id}/source"


def format_pages(start_page: int | None, end_page: int | None) -> str:
    if start_page is None and end_page is None:
        return "n/a"
    if start_page == end_page or end_page is None:
        return str(start_page)
    if start_page is None:
        return str(end_page)
    return f"{start_page}-{end_page}"


def truncate_text(text: str, max_chars: int) -> str:
    normalized = text.strip()
    if max_chars <= 0 or len(normalized) <= max_chars:
        return normalized
    return f"{normalized[: max_chars - 1].rstrip()}..."


def _json_block(value: Any) -> str:
    return "```json\n" + json.dumps(value, indent=2, ensure_ascii=False, default=str) + "\n```"


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run retrieval for one query and dump LLM-judge-friendly evidence.",
    )
    parser.add_argument("ticker", help="Company ticker, e.g. AAPL.")
    parser.add_argument("question", help="Natural-language research question.")
    parser.add_argument(
        "--max-chunks",
        type=int,
        default=None,
        help="Optional cap on printed top chunks. Defaults to retrieval_top_k.",
    )
    parser.add_argument(
        "--max-chars",
        type=int,
        default=DEFAULT_MAX_CHARS,
        help=f"Maximum characters of chunk text to print. Defaults to {DEFAULT_MAX_CHARS}.",
    )
    parser.add_argument(
        "--full-chunks",
        action="store_true",
        help="Print full chunk text instead of truncating.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Print machine-readable JSON instead of Markdown.",
    )
    return parser


def main(argv: Iterable[str] | None = None) -> int:
    args = _build_parser().parse_args(list(argv) if argv is not None else None)
    session = get_sessionmaker()()
    try:
        dump = build_retrieval_dump(
            session,
            ticker=args.ticker,
            question=args.question,
            max_chunks=args.max_chunks,
        )
    finally:
        session.close()

    if args.json_output:
        print(json.dumps(retrieval_dump_to_jsonable(dump), indent=2, ensure_ascii=False))
    else:
        print(
            format_retrieval_dump_markdown(
                dump,
                max_chars=args.max_chars,
                include_full_text=args.full_chunks,
            )
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

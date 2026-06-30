"""Consolidated evaluation report: runs every harness against the live DB/LLM and
prints one performance profile (markdown + JSON). The deployed safety gates
(answerability + entailment) are on for the answer suite, matching production.
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any

from sqlalchemy import text

from app.db import get_sessionmaker
from app.evals import (
    agent_trajectory_eval,
    answer_eval,
    query_planner_eval,
    retrieval_gold_eval,
    retrieval_recall_eval,
)

EVALS_DIR = Path(__file__).resolve().parents[2] / "evals"


def corpus_stats() -> dict[str, int]:
    with get_sessionmaker()() as db:
        row = db.execute(
            text(
                """
                select count(distinct c.id) companies,
                       count(distinct f.id) filings,
                       count(distinct dc.id) chunks,
                       count(distinct ff.id) facts
                from companies c
                left join filings f on f.company_id = c.id
                left join document_chunks dc on dc.filing_id = f.id
                left join financial_facts ff on ff.company_id = c.id
                """
            )
        ).one()
        parsed = db.execute(
            text(
                "select count(distinct f.id) from filings f "
                "join document_chunks dc on dc.filing_id = f.id"
            )
        ).scalar()
    return {
        "companies": row.companies,
        "filings_ingested": row.filings,
        "filings_parsed": parsed,
        "chunks": row.chunks,
        "xbrl_facts": row.facts,
    }


def _percentile(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    rank = max(0, min(len(ordered) - 1, math.ceil(p / 100 * len(ordered)) - 1))
    return ordered[rank]


def gather_report() -> dict[str, Any]:
    answer = answer_eval.run_eval_file(EVALS_DIR / "answer_gold_eval.json")
    answer_cases = json.loads((EVALS_DIR / "answer_gold_eval.json").read_text())["cases"]
    numeric_ids = {c["id"] for c in answer_cases if c.get("expect_values")}
    refusal_ids = {
        c["id"]
        for c in answer_cases
        if c.get("expect_validation_status") == "insufficient_evidence"
    }
    by_id = {r.case_id: r for r in answer.results}

    numeric = [by_id[i] for i in numeric_ids if i in by_id]
    structured_hits = sum(
        1 for r in numeric if not any(f.code == "value_mismatch" for f in r.failures)
    )
    refusal = [by_id[i] for i in refusal_ids if i in by_id]
    refusal_hits = sum(1 for r in refusal if r.passed)
    durations = [r.duration_ms for r in answer.results if r.duration_ms]

    recall = retrieval_recall_eval.run_eval_file(EVALS_DIR / "retrieval_recall_eval.json")
    role = retrieval_gold_eval.run_eval_file(EVALS_DIR / "retrieval_gold_eval.json")
    agent = agent_trajectory_eval.run_eval_file(EVALS_DIR / "agent_trajectory_eval.json")
    planner = query_planner_eval.run_eval_file(
        EVALS_DIR / "query_planner_ambiguous_slot_eval.json"
    )

    def metric(value: float, n: int) -> dict[str, Any]:
        return {"value": value, "n": n}

    from collections import Counter

    planner_field_mismatches = Counter(
        m.field for r in planner.results for m in r.mismatches
    )
    details = {
        "answer_failures": [
            {"id": r.case_id, "codes": [f.code for f in r.failures]}
            for r in answer.results
            if not r.passed
        ],
        "recall_misses": [r.case_id for r in recall.results if not r.passed],
        "role_failures": [r.case_id for r in role.results if not r.passed],
        "planner_top_mismatched_fields": planner_field_mismatches.most_common(8),
    }

    return {
        "corpus": corpus_stats(),
        "details": details,
        "metrics": {
            "retrieval_recall_at_5": metric(recall.recall_at(5), len(recall.results)),
            "retrieval_recall_at_10": metric(recall.recall_at(10), len(recall.results)),
            "retrieval_role_recall_pass_rate": metric(role.pass_rate, len(role.results)),
            "structured_metric_accuracy": metric(
                structured_hits / len(numeric) if numeric else 0.0, len(numeric)
            ),
            "answer_suite_pass_rate": metric(answer.pass_rate, len(answer.results)),
            "mean_claim_citation_coverage": metric(
                answer.mean_claim_citation_coverage, len(answer.results)
            ),
            "contradicted_claims_total": metric(
                answer.total_contradicted_claims, len(answer.results)
            ),
            "unsupported_claims_total": metric(
                answer.total_unsupported_claims, len(answer.results)
            ),
            "safe_refusal_rate": metric(
                refusal_hits / len(refusal) if refusal else 0.0, len(refusal)
            ),
            "planner_slot_field_accuracy": metric(
                planner.field_accuracy, len(planner.results)
            ),
            "planner_case_pass_rate": metric(planner.pass_rate, len(planner.results)),
            "agent_tool_accuracy": metric(agent.pass_rate, len(agent.results)),
            "agent_tool_stability": metric(agent.mean_stability, len(agent.results)),
            "latency_ms_p50": metric(_percentile(durations, 50), len(durations)),
            "latency_ms_p95": metric(_percentile(durations, 95), len(durations)),
            "latency_ms_mean": metric(
                sum(durations) / len(durations) if durations else 0.0, len(durations)
            ),
        },
    }


def format_report(report: dict[str, Any]) -> str:
    corpus = report["corpus"]
    metrics = report["metrics"]
    lines = [
        "# Evaluation report",
        "",
        f"Corpus: {corpus['companies']} companies, "
        f"{corpus['filings_parsed']} parsed filings ({corpus['filings_ingested']} ingested), "
        f"{corpus['chunks']} chunks, {corpus['xbrl_facts']} XBRL facts.",
        "",
        "| Metric | Value | n |",
        "| --- | --- | --- |",
    ]

    def row(label: str, key: str, kind: str) -> str:
        m = metrics[key]
        v = m["value"]
        if kind == "pct":
            shown = f"{v:.1%}"
        elif kind == "ms":
            shown = f"{v:,.0f} ms"
        else:
            shown = f"{v:g}"
        return f"| {label} | {shown} | {m['n']} |"

    lines += [
        row("Text retrieval recall@5", "retrieval_recall_at_5", "pct"),
        row("Text retrieval recall@10", "retrieval_recall_at_10", "pct"),
        row("Evidence-role recall pass-rate", "retrieval_role_recall_pass_rate", "pct"),
        row("Structured-metric accuracy (vs SEC XBRL)", "structured_metric_accuracy", "pct"),
        row("Answer suite pass-rate", "answer_suite_pass_rate", "pct"),
        row("Mean claim-citation coverage", "mean_claim_citation_coverage", "pct"),
        row("Contradicted claims (faithfulness)", "contradicted_claims_total", "count"),
        row("Unsupported (neutral) claims", "unsupported_claims_total", "count"),
        row("Safe refusal of unanswerable", "safe_refusal_rate", "pct"),
        row("Planner slot field accuracy", "planner_slot_field_accuracy", "pct"),
        row("Planner case pass-rate (strict)", "planner_case_pass_rate", "pct"),
        row("Agent tool-selection accuracy", "agent_tool_accuracy", "pct"),
        row("Agent run-to-run stability", "agent_tool_stability", "pct"),
        row("Latency P50", "latency_ms_p50", "ms"),
        row("Latency P95", "latency_ms_p95", "ms"),
        row("Latency mean", "latency_ms_mean", "ms"),
    ]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run all evals and print a consolidated report.")
    parser.add_argument("--json", action="store_true", dest="json_output")
    args = parser.parse_args(argv)

    report = gather_report()
    if args.json_output:
        print(json.dumps(report, indent=2, ensure_ascii=False))
    else:
        print(format_report(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

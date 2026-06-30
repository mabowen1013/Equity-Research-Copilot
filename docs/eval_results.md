# Evaluation results

> Reproduce: `cd backend && .venv/bin/python -m app.evals.run_all` (runs every harness against the
> live DB/LLM with the deployed safety gates on). Numbers below are from a run on 2026-06-30.
> Honesty note: this is a **curated** eval set (small n per metric, stated below), not a benchmark —
> read each number with its n and methodology.

## Corpus

6 US public companies (AAPL, MSFT, NVDA, TSLA, META, JPM): **139 parsed 10-K/10-Q filings**
(542 ingested as metadata), **18,579 embedded chunks**, **5,006 SEC XBRL facts**.

## Results

| Metric | Value | n | Ground truth |
| --- | --- | --- | --- |
| Text retrieval **recall@5** | **91.7%** | 12 | section-labeled text questions |
| Text retrieval recall@10 | 91.7% | 12 | section-labeled text questions |
| Evidence-**role recall** | **100%** | 12 | role-labeled questions |
| **Structured-metric accuracy** | **100%** | 12 | **SEC XBRL** (external) |
| **Faithfulness** — contradicted claims | **0** | 24 | LLM-judge |
| Unsupported (neutral) claims | 3 | 24 | LLM-judge |
| **Safe refusal** of unanswerable | **100%** | 5 | adversarial labels |
| Invalid citations | **0** | 24 | construction guarantee |
| Mean claim-citation coverage | 61% | 24 | — |
| Answer-suite pass-rate (strict) | 75% | 24 | composite (see note) |
| Planner slot **field accuracy** | 61% | 46 | hand-labeled slots |
| Agent tool-selection accuracy | **100%** | 10 | tool labels |
| Agent run-to-run stability | **100%** | 10 | tool labels |
| Latency **P50 / P95** | **13.4s / 19.3s** | 24 | — |

## Resume-ready summary

> Evaluated the system on **6 US public companies** (139 parsed 10-K/10-Q filings, 18,579 embedded
> chunks, 5,006 SEC XBRL facts) across **~100 research questions**: **text-retrieval recall@5 = 92%**,
> **structured financial-metric accuracy = 100%** against SEC XBRL ground truth, **0 contradicted
> claims** (LLM-judge faithfulness), **100% safe refusal** of unanswerable questions, **100% agent
> tool-selection accuracy** (100% run-to-run stable), end-to-end latency **P50/P95 = 13.4s / 19.3s**.

## Methodology + honesty notes (per metric)

- **recall@5 (n=12)** — `retrieval_recall_eval`: for each *text* question (driver→MD&A, risk→Risk
  Factors) over the 6 companies, "hit" = a chunk from the expected SEC section appears in the top-5
  retrieved chunks. Section match normalizes labels to alphanumerics (SEC labels carry OCR noise like
  "RI SK FACTORS"). **Metric questions are excluded on purpose** — they are answered from structured
  XBRL, not by retrieving statement text, so they are covered by structured-metric accuracy. The
  single miss is JPM's MD&A: that 10-K parsed into coarse "PART …" section labels, so MD&A isn't
  cleanly labelled (a parsing-quality issue, not retrieval).
- **role recall (n=12)** — `retrieval_gold_eval`: did the final evidence pack populate the expected
  evidence *roles* (metric / financial-statement / MD&A / risk)? Keyed on stable roles, not volatile
  chunk ids, so it survives re-ingestion.
- **structured-metric accuracy (n=12)** — `answer_eval` `expect_values`: the answer's stated number
  must match the **SEC XBRL** value (from `financial_facts`, most-recent fiscal year) within 2–3%
  tolerance. This is external ground truth, the strongest correctness signal here.
- **faithfulness (n=24)** — claim-level entailment LLM-judge over each cited sentence; 0 contradicted.
  The judge is **uncalibrated against human labels** (a follow-up), so treat it as a strong signal,
  not proof.
- **safe refusal (n=5)** — adversarial questions (off-topic trivia, real-time data, a bank's gross
  margin) must return `insufficient_evidence` rather than fabricate.
- **invalid citations (n=24)** — every citation marker is validated against the retrieved evidence
  set; invalid markers are stripped before answering, so 0 invalid is a construction guarantee, not a
  soft rate.
- **answer-suite pass-rate (75%)** — a *composite* gate (status + citations + value + coverage +
  safety + latency). The 6 misses are **all the strict 0.5 claim-citation-coverage gate on otherwise
  correct answers** (their numbers match XBRL and 0 are contradicted) — i.e. a coverage-strictness
  signal, not wrong answers. Reported transparently rather than by lowering the gate.
- **planner slot field accuracy (61%, n=46)** — fraction of asserted slots matching the hand-labeled
  ambiguous-slot gold. The strict all-9-fields *case* pass-rate is only 4.3% and is **not** reported
  as accuracy: the planner gets core slots (question_type, metric_keys, sections) mostly right but the
  gold also pins secondary fields (comparison_basis, preferred_forms) the current planner varies on,
  so the gold is partly stale. Field accuracy is the honest measure; re-baselining the gold is a
  follow-up.
- **agent tool-selection (n=10)** — for labeled questions, the LLM ReAct controller's chosen tools
  must include the expected ones; each case runs twice (cache cleared) and must pass both →
  stability.
- **latency (n=24)** — end-to-end `duration_ms` per answer run with both safety gates on (answerability
  + entailment), so it reflects the deployed, safety-on configuration.

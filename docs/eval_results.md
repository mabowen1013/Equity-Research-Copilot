# Evaluation results

> Reproduce: `cd backend && .venv/bin/python -m app.evals.run_all` (the small per-harness suites) and
> `.venv/bin/python -m app.evals.metric_accuracy_eval evals/metric_accuracy_eval.json` (the large,
> auto-generated structured-accuracy run). Numbers from runs on 2026-06-30.
> Honesty note: most per-harness suites are **small curated sets** (read each with its n); the
> headline structured-accuracy number is a **larger auto-generated set with a 95% confidence interval**.

## Corpus

6 US public companies (AAPL, MSFT, NVDA, TSLA, META, JPM): **139 parsed 10-K/10-Q filings**
(542 ingested as metadata), **18,579 embedded chunks**, **5,006 SEC XBRL facts**.

## Results

| Metric | Value | n | Ground truth |
| --- | --- | --- | --- |
| Text retrieval **recall@5** | **91.7%** | 12 | section-labeled text questions |
| Text retrieval recall@10 | 91.7% | 12 | section-labeled text questions |
| Evidence-**role recall** | **100%** | 12 | role-labeled questions |
| **Structured-metric accuracy** (auto, FY 2023–2026) | **86.5%** (95% CI 78–92%) | **96** | **SEC XBRL** (external) |
| Structured-metric accuracy (curated latest-FY subset) | 100% | 12 | SEC XBRL |
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

## How much to trust these numbers

A wall of 100% deserves suspicion. They are **not** all the same kind of "strong" (full reasoning +
interview Q&A in `docs/eval_interview_notes.md`):

- **The strongest, most trustworthy number:** structured-metric accuracy **86.5% (95% CI 78–92%,
  n=96)** — questions auto-generated over every company/metric/recent-FY (no hand-picking), graded
  against external **SEC XBRL** truth, comparing the answer's *primary* dollar figure (so a correct
  prior-year number in a YoY sentence can't mask a wrong headline figure). It is below 100% on
  purpose: the misses are concentrated in **operating cash flow (12/18)** and **older fiscal years
  (FY2023 19/26)** — cash-flow statements are cumulative/YTD and historical-FY lookups sometimes
  drift to the latest year (e.g. AAPL FY2024 answered with FY2025's figure). This is the credible,
  large-n result; the curated n=12 "latest-FY" subset is 100% because recent annual figures are the
  easy case.
- **High because the bar is low / n is tiny / near-deterministic:** safe refusal (n=5, and the cases
  target the gate I built), agent tool-selection + stability (subset criterion + obvious mappings +
  temperature 0, so stability is near-free), role recall (role merely populated), invalid citations
  (true by construction).
- **Faithfulness 0 contradicted:** plausible but the LLM-judge is uncalibrated, so treat as a signal.

That the suite is **not** all green — structured accuracy 86.5%, answer-suite 75%, recall@5 91.7%
(a real JPM miss), planner 61% (strict case-pass 4.3%) — is itself evidence the eval surfaces failures
rather than self-congratulating. To push further: independently-authored sets; harder/multi-hop
questions; exact (not subset) agent matching; human-labeled retrieval relevance; calibrating the judge.

## Resume-ready summary

> Evaluated the system on **6 US public companies** (139 parsed 10-K/10-Q filings, 18,579 embedded
> chunks, 5,006 SEC XBRL facts): **structured financial-metric accuracy = 86.5%** (95% CI 78–92%,
> n=96 auto-generated and graded against **SEC XBRL** ground truth), **text-retrieval recall@5 = 92%**,
> **0 contradicted claims** (LLM-judge faithfulness), **100% safe refusal** of unanswerable questions,
> **100% agent tool-selection accuracy** (run-to-run stable), end-to-end latency **P50/P95 = 13.4s / 19.3s**.

## Methodology + honesty notes (per metric)

- **structured-metric accuracy (n=96)** — `metric_accuracy_eval`: questions auto-generated from
  `financial_facts` for every (company × {revenue, net income, operating income, gross profit,
  operating/free cash flow} × latest 3 fiscal years), so the set is not hand-picked. Truth = the SEC
  XBRL value; graded by comparing the answer's **primary (first) dollar figure** within 3% tolerance
  (deliberately strict — "any number in the text" would let a YoY comparison figure mask a wrong
  headline). Run with safety gates off to isolate numeric correctness (0 declined). Only the latest 3
  FYs are used because older XBRL facts carry occasional mis-tags. Reported with a Wilson 95% CI.
  Known weak spots surfaced: operating cash flow (cumulative/YTD semantics) and older fiscal years
  (period drift to the latest year).
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

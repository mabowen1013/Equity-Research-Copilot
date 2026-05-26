# Retrieval Evaluation Dump

## Query

- ticker: `AAPL`
- question: Tell me what matters in Apple's latest 10-Q.
- generated_at_utc: `2026-05-26T01:05:12.443090+00:00`

## Suggested Judge Instructions

Rate each chunk from 0 to 3:

- 3 = directly answers the question with core evidence
- 2 = useful supporting context
- 1 = superficially related but not enough to support an answer
- 0 = wrong metric, period, section, company, or noise

Then answer:

- Are the top chunks sufficient to answer the question?
- Is the evidence pack missing any required role, metric, period, or source?
- Which chunk ids should be treated as gold evidence for this query?

## Retrieval Plan

```json
"question_type='filing_summary' target_sections=['Financial Statements', \"Management's Discussion and Analysis\", 'Liquidity', 'Cash Flows'] metric_keys=['revenue', 'gross_margin', 'operating_income', 'net_income', 'operating_cash_flow', 'free_cash_flow'] time_scope='latest' comparison_basis='none' comparison_candidates=[] default_comparison_basis=None ambiguities=[] forms=['10-Q'] preferred_forms=['10-Q'] dense_queries=['latest most recent filing period filing summary earnings report key takeaways financial results business overview revenue total net sales net sales gross margin gross profit operating income income from operations net income net earnings operating cash flow net cash provided by operating activities', 'latest most recent filing period financial statements statements of cash flows operating activities revenue total net sales net sales condensed consolidated statements of operations gross margin gross profit operating income income from operations statements of operations net income net earnings operating cash flow', 'management discussion analysis results of operations drivers reasons primarily due to higher lower revenue total net sales net sales gross margin gross profit operating income income from operations net income net earnings operating cash flow net cash provided by operating activities free cash flow', \"Tell me what matters in Apple's latest 10-Q.\"] dense_query_specs=[{'role': 'slot', 'text': 'latest most recent filing period filing summary earnings report key takeaways financial results business overview revenue total net sales net sales gross margin gross profit operating income income from operations net income net earnings operating cash flow net cash provided by operating activities', 'weight': 1.0}, {'role': 'financial_statement', 'text': 'latest most recent filing period financial statements statements of cash flows operating activities revenue total net sales net sales condensed consolidated statements of operations gross margin gross profit operating income income from operations statements of operations net income net earnings operating cash flow', 'weight': 0.9}, {'role': 'mda_drivers', 'text': 'management discussion analysis results of operations drivers reasons primarily due to higher lower revenue total net sales net sales gross margin gross profit operating income income from operations net income net earnings operating cash flow net cash provided by operating activities free cash flow', 'weight': 0.85}, {'role': 'original', 'text': \"Tell me what matters in Apple's latest 10-Q.\", 'weight': 0.4}] lexical_queries=['\"total net sales\"', '\"net sales\"', '\"revenue growth\"', '\"net sales increased\"', '\"net sales decreased\"', '\"total net sales increased\"', '\"total net sales decreased\"', '\"revenue from contract\"', '\"condensed consolidated statements of operations\"', '\"consolidated statements of operations\"', '\"three months ended\" \"net sales\"', '\"six months ended\" \"net sales\"', '\"year ended\" \"net sales\"', '\"gross margin\"', '\"gross margin increased\"', '\"gross margin decreased\"', '\"gross profit\"', '\"cost of sales\"', '\"operating income\"', '\"operating income increased\"', '\"operating income decreased\"', '\"income from operations\"', '\"statements of operations\"', '\"net income\"', '\"net income increased\"', '\"net income decreased\"', '\"net earnings\"', '\"net cash provided by operating activities\"', '\"cash provided by operating activities\"', '\"operating activities\"', '\"statements of cash flows\"', '\"free cash flow\"', '\"capital expenditures\"', '\"payments for acquisition of property plant and equipment\"', '\"management discussion and analysis\"', '\"results of operations\"', '\"liquidity and capital resources\"', '\"risk factors\"'] rule_confidence=0.85 matched_rules=['planner:llm_fallback', 'llm_fallback:ambiguous_intent', 'metric:revenue:llm', 'metric:gross_margin:llm', 'metric:operating_income:llm', 'metric:net_income:llm', 'metric:operating_cash_flow:llm', 'metric:free_cash_flow:llm'] planner_source='llm_validated' confidence_breakdown={'intent_confidence': 0.85, 'metric_confidence': 0.85, 'time_confidence': 0.85, 'strategy_confidence': 0.85, 'validation_confidence': 0.85, 'overall_confidence': 0.85} needs_financial_facts=True needs_text_chunks=True needs_metric_comparisons=False evidence_roles=['primary_financial_statement_chunks', 'mda_explanation_chunks'] requires_llm_fallback_reason='ambiguous_intent'"
```

## Source Coverage

```json
{
  "chunk_count": 10,
  "fact_count": 36,
  "metric_comparison_count": 0,
  "evidence_span_count": 9,
  "forms": [
    "10-Q"
  ],
  "sections": [
    "PART I - ITEM 1 - Financial Statements",
    "PART I - ITEM 2 - Management’s Discussion and Analysis of Financial Condition and Results of Operations"
  ],
  "latest_chunk_filing_date": "2026-05-01",
  "fact_metric_keys": [
    "free_cash_flow",
    "gross_margin",
    "net_income",
    "operating_cash_flow",
    "operating_income",
    "revenue"
  ],
  "comparison_bases": []
}
```

## Candidate Counts

```json
{
  "dense": 40,
  "lexical": 40,
  "facts": 36,
  "comparison_facts": 36,
  "metric_comparisons": 0,
  "fused_chunks": 61,
  "evidence_chunk_candidates": 10,
  "evidence_span_candidates": 34,
  "selected_evidence_spans": 9
}
```

## Chunk Scope

```json
{
  "latest_filing_date": "2026-05-01",
  "reason": "time_scope:latest"
}
```

## Dense Query Sources

```json
[
  {
    "source": "dense:slot",
    "candidate_count": 40,
    "weight": 1.0
  },
  {
    "source": "dense:financial_statement",
    "candidate_count": 40,
    "weight": 0.9
  },
  {
    "source": "dense:mda_drivers",
    "candidate_count": 40,
    "weight": 0.85
  },
  {
    "source": "dense:original",
    "candidate_count": 40,
    "weight": 0.4
  }
]
```

## Metric Comparisons

No metric comparisons returned.

## Final Evidence Pack

- metric_comparisons: 0
- primary_financial_statement_chunks: chunk:1252, chunk:1258
- mda_explanation_chunks: chunk:1279, chunk:1277, chunk:1281
- segment_or_product_breakdown_chunks: chunk:1280, chunk:1282
- annual_context_chunks: none
- primary_financial_statement_spans: span:1252:primary_financial_statement_chunks:0:679, span:1252:primary_financial_statement_chunks:680:1333
- mda_explanation_spans: span:1279:mda_explanation_chunks:966:1052, span:1279:mda_explanation_chunks:833:965, span:1277:mda_explanation_chunks:904:1005, span:1277:mda_explanation_chunks:1603:1709
- segment_or_product_breakdown_spans: span:1280:segment_or_product_breakdown_chunks:437:1323, span:1282:segment_or_product_breakdown_chunks:0:713, span:1282:segment_or_product_breakdown_chunks:510:559
- annual_context_spans: none

## Top Retrieved Chunks

### Rank 1: chunk:1279

- evidence_id: `chunk:1279`
- highlighted_source: `/filings/1/chunks/1279/source`
- sec_source: `https://www.sec.gov/Archives/edgar/data/320193/000032019326000013/aapl-20260328.htm`
- score: 0.520749
- fusion_score: 0.060749
- source_ranks: `{"dense:slot": 8, "dense:financial_statement": 15, "dense:mda_drivers": 3, "dense:original": 9, "lexical": 1}`
- rerank_boosts: `{"section_match": 0.15, "latest_filing": 0.1, "form_priority": 0.04, "table_metric_context": 0.03, "strong_metric_match": 0.08, "statement_context_match": 0.06}`
- form_type: `10-Q`
- filing_date: `2026-05-01`
- section: PART I - ITEM 2 - Management’s Discussion and Analysis of Financial Condition and Results of Operations
- pages: 17

```text
**Products and Services Performance**
The following table shows net sales by category for the three- and six-month periods ended March 28, 2026 and March 29, 2025 (dollars in millions):
||Three Months Ended — March 28, 2026|March 29, 2025|Change|Six Months Ended — March 28, 2026|March 29, 2025|Change|
|---|---|---|---|---|---|---|
|iPhone|$ 56,994|$ 46,841|22 %|$ 142,263|$ 115,979|23 %|
|Mac|8,399|7,949|6 %|16,785|16,936|(1) %|
|iPad|6,914|6,402|8 %|15,509|14,490|7 %|
|Wearables, Home and Accessories|7,901|7,522|5 %|19,394|19,269|1 %|
|Services|30,976|26,645|16 %|60,989|52,985|15 %|
|Total net sales|$ 111,184|$ 95,359|17 %|$ 254,940|$ 219,659|16 %|
*iPhone*

iPhone net sales increased during the second quarter and first six months of 2026 compared to the same periods in 2025 due to higher net sales of Pro models.

*Mac*

Mac net sales increased during the second quarter of 2026 compared to the second quarter of 2025 due to higher net sales of laptops. Year-over-year Mac net sales during the first six months of 2026 were relatively flat.

*iPad*

iPad net sales increased during the second quarter and first six months of 2026 compared to the same periods in 2025 primarily due to higher net sales of iPad, partially offset by lower net sales of iPad mini ® .
*Wearables, Home and Accessories*

Wearables, Home and Accessories net sales increased during the second quarter of 2026 compared to the second quarter of 2025 primarily due to higher net sales of Accessories and Wearables.
```

### Rank 2: chunk:1277

- evidence_id: `chunk:1277`
- highlighted_source: `/filings/1/chunks/1277/source`
- sec_source: `https://www.sec.gov/Archives/edgar/data/320193/000032019326000013/aapl-20260328.htm`
- score: 0.520584
- fusion_score: 0.060584
- source_ranks: `{"dense:slot": 6, "dense:financial_statement": 14, "dense:mda_drivers": 1, "dense:original": 23, "lexical": 2}`
- rerank_boosts: `{"section_match": 0.15, "latest_filing": 0.1, "form_priority": 0.04, "table_metric_context": 0.03, "strong_metric_match": 0.08, "statement_context_match": 0.06}`
- form_type: `10-Q`
- filing_date: `2026-05-01`
- section: PART I - ITEM 2 - Management’s Discussion and Analysis of Financial Condition and Results of Operations
- pages: 16

```text
**Segment Operating Performance**
The following table shows net sales by reportable segment for the three- and six-month periods ended March 28, 2026 and March 29, 2025 (dollars in millions):
||Three Months Ended — March 28, 2026|March 29, 2025|Change|Six Months Ended — March 28, 2026|March 29, 2025|Change|
|---|---|---|---|---|---|---|
|Americas|$ 45,093|$ 40,315|12 %|$ 103,622|$ 92,963|11 %|
|Europe|28,055|24,454|15 %|66,201|58,315|14 %|
|Greater China|20,497|16,002|28 %|46,023|34,515|33 %|
|Japan|8,401|7,298|15 %|17,814|16,285|9 %|
|Rest of Asia Pacific|9,138|7,290|25 %|21,280|17,581|21 %|
|Total net sales|$ 111,184|$ 95,359|17 %|$ 254,940|$ 219,659|16 %|
*Americas*

Americas net sales increased during the second quarter and first six months of 2026 compared to the same periods in 2025 due to higher net sales of iPhone and Services. The strength in foreign currencies relative to the U.S. dollar had a favorable year-over-year impact on Americas net sales during the second quarter of 2026.

*Europe*

Europe net sales increased during the second quarter and first six months of 2026 compared to the same periods in 2025 primarily due to higher net sales of iPhone and Services. The strength in foreign currencies relative to the U.S. dollar had a net favorable year-over-year impact on Europe net sales during the second quarter and first six months of 2026.
*Greater China*

Greater China net sales increased during the second quarter and first six months of 2026 compared to the same periods in 2025 due to higher net sales of iPhone. The strength in the renminbi relative to the U.S. dollar had a favorable year-over-year impact on Greater China net sales during the second quarter of 2026.
```

### Rank 3: chunk:1273

- evidence_id: `chunk:1273`
- highlighted_source: `/filings/1/chunks/1273/source`
- sec_source: `https://www.sec.gov/Archives/edgar/data/320193/000032019326000013/aapl-20260328.htm`
- score: 0.519541
- fusion_score: 0.059541
- source_ranks: `{"dense:slot": 2, "dense:financial_statement": 4, "dense:mda_drivers": 6, "dense:original": 8, "lexical": 25}`
- rerank_boosts: `{"section_match": 0.15, "latest_filing": 0.1, "form_priority": 0.04, "table_metric_context": 0.03, "strong_metric_match": 0.08, "statement_context_match": 0.06}`
- form_type: `10-Q`
- filing_date: `2026-05-01`
- section: PART I - ITEM 1 - Financial Statements
- pages: 14

```text
||Six Months Ended March 28, 2026 — Americas|Europe|Greater China|Japan|Rest of Asia Pacific|Corporate|Total|
|---|---|---|---|---|---|---|---|---|
|Net sales|$ 103,622|$ 66,201|$ 46,023|$ 17,814|$ 21,280|$ —|$ 254,940|
|Cost of sales|( 54,963 )|( 32,817 )|( 23,663 )|( 8,778 )|( 10,707 )|—|( 130,928 )|
|Research and development|—|—|—|—|—|( 22,306 )|( 22,306 )|
|Selling and marketing|( 5,333 )|( 2,542 )|( 1,379 )|( 584 )|( 760 )|—|( 10,598 )|
|General and administrative|—|—|—|—|—|( 4,371 )|( 4,371 )|
|Operating income/(loss)|$ 43,326|$ 30,842|$ 20,981|$ 8,452|$ 9,813|$ ( 26,677 )|$ 86,737|
||Six Months Ended March 29, 2025|||||||
||Americas|Europe|Greater China|Japan|Rest of Asia Pacific|Corporate|Total|
|Net sales|$ 92,963|$ 58,315|$ 34,515|$ 16,285|$ 17,581|$ —|$ 219,659|
|Cost of sales|( 49,589 )|( 31,068 )|( 18,553 )|( 8,003 )|( 9,304 )|—|( 116,517 )|
|Research and development|—|—|—|—|—|( 16,818 )|( 16,818 )|
|Selling and marketing|( 5,091 )|( 2,324 )|( 1,176 )|( 534 )|( 707 )|—|( 9,832 )|
|General and administrative|—|—|—|—|—|( 4,071 )|( 4,071 )|
|Operating income/(loss)|$ 38,283|$ 24,923|$ 14,786|$ 7,748|$ 7,570|$ ( 20,889 )|$ 72,421|
```

### Rank 4: chunk:1252

- evidence_id: `chunk:1252`
- highlighted_source: `/filings/1/chunks/1252/source`
- sec_source: `https://www.sec.gov/Archives/edgar/data/320193/000032019326000013/aapl-20260328.htm`
- score: 0.518428
- fusion_score: 0.058428
- source_ranks: `{"dense:slot": 4, "dense:financial_statement": 2, "dense:mda_drivers": 11, "dense:original": 7, "lexical": 27}`
- rerank_boosts: `{"section_match": 0.15, "latest_filing": 0.1, "form_priority": 0.04, "table_metric_context": 0.03, "strong_metric_match": 0.08, "statement_context_match": 0.06}`
- form_type: `10-Q`
- filing_date: `2026-05-01`
- section: PART I - ITEM 1 - Financial Statements
- pages: 3

```text
**PART I — FINANCIAL INFORMATION**
**Item 1. Financial Statements**
**Apple Inc.**
**CONDENSED CONSOLIDATED STATEMENTS OF OPERATIONS (Unaudited)**
(In millions, except number of shares, which are reflected in thousands, and per-share amounts)
||Three Months Ended — March 28, 2026|March 29, 2025|Six Months Ended — March 28, 2026|March 29, 2025|
|---|---|---|---|---|
|Net sales:|||||
|Products|$ 80,208|$ 68,714|$ 193,951|$ 166,674|
|Services|30,976|26,645|60,989|52,985|
|Total net sales|111,184|95,359|254,940|219,659|
|Cost of sales:|||||
|Products|49,179|44,030|116,657|103,477|
|Services|7,224|6,462|14,271|13,040|
|Total cost of sales|56,403|50,492|130,928|116,517|
|Gross margin|54,781|44,867|124,012|103,142|
|Operating expenses:|||||
|Research and development|11,419|8,550|22,306|16,818|
|Selling, general and administrative|7,477|6,728|14,969|13,903|
|Total operating expenses|18,896|15,278|37,275|30,721|
|Operating income|35,885|29,589|86,737|72,421|
|Other income/(expense), net|( 52 )|( 279 )|98|( 527 )|
|Income before provision for income taxes|35,833|29,310|86,835|71,894|
|Provision for income taxes|6,255|4,530|15,160|10,784|
|Net income|$ 29,578|$ 24,780|$ 71,675|$ 61,110|
|Earnings per share:|||||
|Basic|$ 2.02|$ 1.65|$ 4.87|$ 4.06|
|Diluted|$ 2.01|$ 1.65|$ 4.85|$ 4.05|
|Shares used in computing earnings per share:|||||
|Basic|14,673,278|14,994,082|14,710,718|15,037,903|
|Diluted|14,725,873|15,056,133|14,768,115|15,103,499|
```

### Rank 5: chunk:1272

- evidence_id: `chunk:1272`
- highlighted_source: `/filings/1/chunks/1272/source`
- sec_source: `https://www.sec.gov/Archives/edgar/data/320193/000032019326000013/aapl-20260328.htm`
- score: 0.518231
- fusion_score: 0.058231
- source_ranks: `{"dense:slot": 3, "dense:financial_statement": 9, "dense:mda_drivers": 8, "dense:original": 3, "lexical": 26}`
- rerank_boosts: `{"section_match": 0.15, "latest_filing": 0.1, "form_priority": 0.04, "table_metric_context": 0.03, "strong_metric_match": 0.08, "statement_context_match": 0.06}`
- form_type: `10-Q`
- filing_date: `2026-05-01`
- section: PART I - ITEM 1 - Financial Statements
- pages: 14

```text
**Note 10 – Segment Information**
The following tables show information by reportable segment for the three- and six-month periods ended March 28, 2026 and March 29, 2025 (in millions):
||Three Months Ended March 28, 2026 — Americas|Europe|Greater China|Japan|Rest of Asia Pacific|Corporate|Total|
|---|---|---|---|---|---|---|---|
|Net sales|$ 45,093|$ 28,055|$ 20,497|$ 8,401|$ 9,138|$ —|$ 111,184|
|Cost of sales|( 23,114 )|( 13,756 )|( 10,633 )|( 4,267 )|( 4,633 )|—|( 56,403 )|
|Research and development|—|—|—|—|—|( 11,419 )|( 11,419 )|
|Selling and marketing|( 2,606 )|( 1,247 )|( 675 )|( 295 )|( 378 )|—|( 5,201 )|
|General and administrative|—|—|—|—|—|( 2,276 )|( 2,276 )|
|Operating income/(loss)|$ 19,373|$ 13,052|$ 9,189|$ 3,839|$ 4,127|$ ( 13,695 )|$ 35,885|
||Three Months Ended March 29, 2025|||||||
||Americas|Europe|Greater China|Japan|Rest of Asia Pacific|Corporate|Total|
|Net sales|$ 40,315|$ 24,454|$ 16,002|$ 7,298|$ 7,290|$ —|$ 95,359|
|Cost of sales|( 21,094 )|( 13,025 )|( 8,794 )|( 3,610 )|( 3,969 )|—|( 50,492 )|
|Research and development|—|—|—|—|—|( 8,550 )|( 8,550 )|
|Selling and marketing|( 2,447 )|( 1,113 )|( 582 )|( 254 )|( 335 )|—|( 4,731 )|
|General and administrative|—|—|—|—|—|( 1,997 )|( 1,997 )|
|Operating income/(loss)|$ 16,774|$ 10,316|$ 6,626|$ 3,434|$ 2,986|$ ( 10,547 )|$ 29,589|
```

### Rank 6: chunk:1258

- evidence_id: `chunk:1258`
- highlighted_source: `/filings/1/chunks/1258/source`
- sec_source: `https://www.sec.gov/Archives/edgar/data/320193/000032019326000013/aapl-20260328.htm`
- score: 0.514111
- fusion_score: 0.054111
- source_ranks: `{"dense:slot": 5, "dense:financial_statement": 1, "dense:mda_drivers": 13, "lexical": 13}`
- rerank_boosts: `{"section_match": 0.15, "latest_filing": 0.1, "form_priority": 0.04, "table_metric_context": 0.03, "strong_metric_match": 0.08, "statement_context_match": 0.06}`
- form_type: `10-Q`
- filing_date: `2026-05-01`
- section: PART I - ITEM 1 - Financial Statements
- pages: 7

```text
**Apple Inc.**
**CONDENSED CONSOLIDATED STATEMENTS OF CASH FLOWS (Unaudited)**
(In millions)
||Six Months Ended — March 28, 2026|March 29, 2025|
|---|---|---|
|Cash, cash equivalents, and restricted cash and cash equivalents, beginning balances|$ 35,934|$ 29,943|
|Operating activities:|||
|Net income|71,675|61,110|
|Adjustments to reconcile net income to cash generated by operating activities:|||
|Depreciation and amortization|6,653|5,741|
|Share-based compensation expense|7,122|6,512|
|Other|( 1,717 )|( 2,217 )|
|Changes in operating assets and liabilities:|||
|Accounts receivable, net|9,295|7,266|
|Vendor non-trade receivables|10,008|9,171|
|Inventories|( 1,084 )|858|
|Other current and non-current assets|( 14,329 )|( 4,371 )|
|Accounts payable|( 12,297 )|( 14,604 )|
|Other current and non-current liabilities|7,301|( 15,579 )|
|Cash generated by operating activities|82,627|53,887|
|Investing activities:|||
|Purchases of marketable securities|( 32,432 )|( 12,442 )|
|Proceeds from maturities of marketable securities|18,691|26,587|
|Proceeds from sales of marketable securities|8,615|5,210|
|Payments for acquisition of property, plant and equipment|( 4,344 )|( 6,011 )|
|Other|( 1,584 )|( 635 )|
|Cash generated by/(used in) investing activities|( 11,054 )|12,709|
|Financing activities:|||
|Payments for taxes related to net share settlement of equity awards|( 3,252 )|( 3,205 )|
|Payments for dividends and dividend equivalents|( 7,743 )|( 7,614 )|
|Repurchases of common stock|( 36,989 )|( 49,504 )|
|Repayments of term debt|( 7,914 )|( 4,009 )|
|Repayments of commercial paper, net|( 5,911 )|( 3,968 )|
|Other|( 126 )|( 77 )|
|Cash used in financing activities|( 61,935 )|( 68,377 )|
|Increase/(Decrease) in cash, cash equivalents, and restricted cash and cash equivalents|9,638|( 1,781 )|
|Cash, cash equivalents, and restricted cash and cash equivalents, ending balances|$ 45,572|$ 28,162|
|Supplemental cash flow disclosure:|||
|Cash paid for income taxes, net|$ 20,397|$ 31,683|
```

### Rank 7: chunk:1282

- evidence_id: `chunk:1282`
- highlighted_source: `/filings/1/chunks/1282/source`
- sec_source: `https://www.sec.gov/Archives/edgar/data/320193/000032019326000013/aapl-20260328.htm`
- score: 0.510967
- fusion_score: 0.050967
- source_ranks: `{"dense:slot": 16, "dense:financial_statement": 17, "dense:mda_drivers": 5, "lexical": 9}`
- rerank_boosts: `{"section_match": 0.15, "latest_filing": 0.1, "form_priority": 0.04, "table_metric_context": 0.03, "strong_metric_match": 0.08, "statement_context_match": 0.06}`
- form_type: `10-Q`
- filing_date: `2026-05-01`
- section: PART I - ITEM 2 - Management’s Discussion and Analysis of Financial Condition and Results of Operations
- pages: 19

```text
**Operating Expenses**
Operating expenses for the three- and six-month periods ended March 28, 2026 and March 29, 2025, were as follows (dollars in millions):
||Three Months Ended — March 28, 2026|March 29, 2025|Change|Six Months Ended — March 28, 2026|March 29, 2025|Change|
|---|---|---|---|---|---|---|
|Research and development|$ 11,419|$ 8,550|34 %|$ 22,306|$ 16,818|33 %|
|Percentage of total net sales|10 %|9 %||9 %|8 %||
|Selling, general and administrative|$ 7,477|$ 6,728|11 %|$ 14,969|$ 13,903|8 %|
|Percentage of total net sales|7 %|7 %||6 %|6 %||
|Total operating expenses|$ 18,896|$ 15,278|24 %|$ 37,275|$ 30,721|21 %|
|Percentage of total net sales|17 %|16 %||15 %|14 %||
*Research and Development*

Research and development (“R&D”) expense increased during the second quarter and first six months of 2026 compared to the same periods in 2025 primarily due to higher infrastructure-related costs and headcount-related expenses.

*Selling, General and Administrative*

Selling, general and administrative expense increased during the second quarter and first six months of 2026 compared to the same periods in 2025 primarily due to higher headcount-related expenses, variable selling expenses and professional services.
```

### Rank 8: chunk:1281

- evidence_id: `chunk:1281`
- highlighted_source: `/filings/1/chunks/1281/source`
- sec_source: `https://www.sec.gov/Archives/edgar/data/320193/000032019326000013/aapl-20260328.htm`
- score: 0.510723
- fusion_score: 0.050723
- source_ranks: `{"dense:slot": 20, "dense:financial_statement": 28, "dense:mda_drivers": 2, "lexical": 3}`
- rerank_boosts: `{"section_match": 0.15, "latest_filing": 0.1, "form_priority": 0.04, "table_metric_context": 0.03, "strong_metric_match": 0.08, "statement_context_match": 0.06}`
- form_type: `10-Q`
- filing_date: `2026-05-01`
- section: PART I - ITEM 2 - Management’s Discussion and Analysis of Financial Condition and Results of Operations
- pages: 18

```text
|Gross margin percentage: — Products|38.7 %|35.9 %|39.9 %|37.9 %|
|---|---|---|---|---|---|
|Services|76.7 %|75.7 %|76.6 %|75.4 %|
|Total gross margin percentage|49.3 %|47.1 %|48.6 %|47.0 %|
*Products Gross Margin*

Products gross margin and gross margin percentage increased during the second quarter and first six months of 2026 compared to the same periods in 2025 primarily due to a different mix of products and strength in foreign currencies relative to the U.S. dollar, partially offset by higher costs.

*Services Gross Margin*

Services gross margin increased during the second quarter and first six months of 2026 compared to the same periods in 2025 primarily due to higher Services net sales and a different mix of services.
Services gross margin percentage increased during the second quarter and first six months of 2026 compared to the same periods in 2025 primarily due to a different mix of services and strength in foreign currencies relative to the U.S. dollar, partially offset by higher costs. The Company’s future gross margins can be impacted by a variety of factors, as discussed in Part I, Item 1A of the 2025 Form 10-K and Part II, Item 1A of this Form 10-Q, in each case under the heading “Risk Factors.” As a result, the Company believes, in general, gross margins will be subject to volatility and downward pressure.
```

### Rank 9: chunk:1280

- evidence_id: `chunk:1280`
- highlighted_source: `/filings/1/chunks/1280/source`
- sec_source: `https://www.sec.gov/Archives/edgar/data/320193/000032019326000013/aapl-20260328.htm`
- score: 0.509737
- fusion_score: 0.049737
- source_ranks: `{"dense:slot": 23, "dense:financial_statement": 27, "dense:mda_drivers": 4, "lexical": 4}`
- rerank_boosts: `{"section_match": 0.15, "latest_filing": 0.1, "form_priority": 0.04, "table_metric_context": 0.03, "strong_metric_match": 0.08, "statement_context_match": 0.06}`
- form_type: `10-Q`
- filing_date: `2026-05-01`
- section: PART I - ITEM 2 - Management’s Discussion and Analysis of Financial Condition and Results of Operations
- pages: 17-18

```text
Year-over-year Mac net sales during the first six months of 2026 were relatively flat.

*iPad*

iPad net sales increased during the second quarter and first six months of 2026 compared to the same periods in 2025 primarily due to higher net sales of iPad, partially offset by lower net sales of iPad mini ® .
Year-over-year Wearables, Home and Accessories net sales during the first six months of 2026 were relatively flat.

*Services*

Services net sales increased during the second quarter and first six months of 2026 compared to the same periods in 2025 primarily due to higher net sales from advertising, the App Store ® and cloud services.
**Gross Margin**
Products and Services gross margin and gross margin percentage for the three- and six-month periods ended March 28, 2026 and March 29, 2025, were as follows (dollars in millions):
||Three Months Ended — March 28, 2026|March 29, 2025|Six Months Ended — March 28, 2026|March 29, 2025|
|---|---|---|---|---|
|Gross margin:|||||
|Products|$ 31,029|$ 24,684|$ 77,294|$ 63,197|
|Services|23,752|20,183|46,718|39,945|
|Total gross margin|$ 54,781|$ 44,867|$ 124,012|$ 103,142|
|Gross margin percentage: — Products|38.7 %|35.9 %|39.9 %|37.9 %|
|---|---|---|---|---|---|
|Services|76.7 %|75.7 %|76.6 %|75.4 %|
|Total gross margin percentage|49.3 %|47.1 %|48.6 %|47.0 %|
```

### Rank 10: chunk:1275

- evidence_id: `chunk:1275`
- highlighted_source: `/filings/1/chunks/1275/source`
- sec_source: `https://www.sec.gov/Archives/edgar/data/320193/000032019326000013/aapl-20260328.htm`
- score: 0.482514
- fusion_score: 0.052514
- source_ranks: `{"dense:slot": 17, "dense:financial_statement": 36, "dense:mda_drivers": 9, "dense:original": 27, "lexical": 8}`
- rerank_boosts: `{"section_match": 0.15, "latest_filing": 0.1, "form_priority": 0.04, "strong_metric_match": 0.08, "statement_context_match": 0.06}`
- form_type: `10-Q`
- filing_date: `2026-05-01`
- section: PART I - ITEM 2 - Management’s Discussion and Analysis of Financial Condition and Results of Operations
- pages: 15-16

```text
**Business Seasonality and Product Introductions**

The Company has historically experienced higher net sales in its first quarter compared to other quarters in its fiscal year due in part to seasonal holiday demand. Additionally, new product and service introductions can significantly impact net sales, cost of sales and operating expenses. The timing of product introductions can also impact the Company’s net sales to its indirect distribution channels as these channels are filled with new inventory following a product launch, and channel inventory of an older product often declines as the launch of a newer product approaches. Net sales can also be affected when consumers and distributors anticipate a product introduction.
During the second quarter of 2026, the Company announced the following new or updated products:

• iPad Air ®

• iPhone 17e

• MacBook Pro ®

• MacBook Air ®

• MacBook Neo™

• AirPods Max ® 2
**Macroeconomic Conditions**

Macroeconomic conditions, including inflation, interest rates, component pricing and currency fluctuations, have directly and indirectly impacted, and could in the future materially impact, the Company’s results of operations and financial condition. The Company is experiencing a period of supply constraints and increasing costs for components driven by factors such as industry supply-demand imbalances for components, including advanced semiconductors, storage (NAND) and memory (DRAM). The Company expects these trends to intensify, which, together with actions that may be taken by the Company in response to such trends, may materially adversely affect demand for the Company’s products and negatively impact the Company’s revenue, costs, gross margin, results of operations and financial condition.
**Tariffs and Other Measures**

Beginning in the second quarter of 2025, new tariffs were announced on imports to the U.S., including additional tariffs on imports from China, India, Japan, South Korea, Taiwan, Vietnam and the European Union (“EU”), among others. In response, several countries have imposed, or threatened to impose, reciprocal tariffs on imports from the U.S. and other retaliatory measures.
```

## Top Financial Facts

### Fact 1: revenue

- evidence_id: `financial_fact:43176`
- label: Revenue from Contract with Customer, Excluding Assessed Tax
- period: Q2 2026 quarter
- value: 111184000000.000000 USD
- form_type: `10-Q`
- source: `https://www.sec.gov/Archives/edgar/data/320193/000032019326000013/0000320193-26-000013-index.htm`

### Fact 2: revenue

- evidence_id: `financial_fact:43175`
- label: Revenue from Contract with Customer, Excluding Assessed Tax
- period: Q2 2026 year-to-date
- value: 254940000000.000000 USD
- form_type: `10-Q`
- source: `https://www.sec.gov/Archives/edgar/data/320193/000032019326000013/0000320193-26-000013-index.htm`

### Fact 3: revenue

- evidence_id: `financial_fact:43174`
- label: Revenue from Contract with Customer, Excluding Assessed Tax
- period: Q1 2026 quarter
- value: 143756000000.000000 USD
- form_type: `10-Q`
- source: `https://www.sec.gov/Archives/edgar/data/320193/000032019326000006/0000320193-26-000006-index.htm`

### Fact 4: revenue

- evidence_id: `financial_fact:43172`
- label: Revenue from Contract with Customer, Excluding Assessed Tax
- period: Q3 2025 quarter
- value: 94036000000.000000 USD
- form_type: `10-Q`
- source: `https://www.sec.gov/Archives/edgar/data/320193/000032019325000073/0000320193-25-000073-index.htm`

### Fact 5: revenue

- evidence_id: `financial_fact:43171`
- label: Revenue from Contract with Customer, Excluding Assessed Tax
- period: Q3 2025 year-to-date
- value: 313695000000.000000 USD
- form_type: `10-Q`
- source: `https://www.sec.gov/Archives/edgar/data/320193/000032019325000073/0000320193-25-000073-index.htm`

### Fact 6: revenue

- evidence_id: `financial_fact:43170`
- label: Revenue from Contract with Customer, Excluding Assessed Tax
- period: Q2 2025 quarter
- value: 95359000000.000000 USD
- form_type: `10-Q`
- source: `https://www.sec.gov/Archives/edgar/data/320193/000032019326000013/0000320193-26-000013-index.htm`

### Fact 7: gross_margin

- evidence_id: `financial_fact:43363`
- label: Gross Margin
- period: Q2 2026 quarter
- value: 0.492706 ratio
- form_type: `10-Q`
- source: `https://www.sec.gov/Archives/edgar/data/320193/000032019326000013/0000320193-26-000013-index.htm`

### Fact 8: gross_margin

- evidence_id: `financial_fact:43362`
- label: Gross Margin
- period: Q2 2026 year-to-date
- value: 0.486436 ratio
- form_type: `10-Q`
- source: `https://www.sec.gov/Archives/edgar/data/320193/000032019326000013/0000320193-26-000013-index.htm`

### Fact 9: gross_margin

- evidence_id: `financial_fact:43361`
- label: Gross Margin
- period: Q1 2026 quarter
- value: 0.481587 ratio
- form_type: `10-Q`
- source: `https://www.sec.gov/Archives/edgar/data/320193/000032019326000006/0000320193-26-000006-index.htm`

### Fact 10: gross_margin

- evidence_id: `financial_fact:43359`
- label: Gross Margin
- period: Q3 2025 quarter
- value: 0.464907 ratio
- form_type: `10-Q`
- source: `https://www.sec.gov/Archives/edgar/data/320193/000032019325000073/0000320193-25-000073-index.htm`

### Fact 11: gross_margin

- evidence_id: `financial_fact:43358`
- label: Gross Margin
- period: Q3 2025 year-to-date
- value: 0.468162 ratio
- form_type: `10-Q`
- source: `https://www.sec.gov/Archives/edgar/data/320193/000032019325000073/0000320193-25-000073-index.htm`

### Fact 12: gross_margin

- evidence_id: `financial_fact:43357`
- label: Gross Margin
- period: Q2 2025 quarter
- value: 0.470506 ratio
- form_type: `10-Q`
- source: `https://www.sec.gov/Archives/edgar/data/320193/000032019326000013/0000320193-26-000013-index.htm`

### Fact 13: operating_income

- evidence_id: `financial_fact:43055`
- label: Operating Income (Loss)
- period: Q2 2026 quarter
- value: 35885000000.000000 USD
- form_type: `10-Q`
- source: `https://www.sec.gov/Archives/edgar/data/320193/000032019326000013/0000320193-26-000013-index.htm`

### Fact 14: operating_income

- evidence_id: `financial_fact:43054`
- label: Operating Income (Loss)
- period: Q2 2026 year-to-date
- value: 86737000000.000000 USD
- form_type: `10-Q`
- source: `https://www.sec.gov/Archives/edgar/data/320193/000032019326000013/0000320193-26-000013-index.htm`

### Fact 15: operating_income

- evidence_id: `financial_fact:43053`
- label: Operating Income (Loss)
- period: Q1 2026 quarter
- value: 50852000000.000000 USD
- form_type: `10-Q`
- source: `https://www.sec.gov/Archives/edgar/data/320193/000032019326000006/0000320193-26-000006-index.htm`

### Fact 16: operating_income

- evidence_id: `financial_fact:43051`
- label: Operating Income (Loss)
- period: Q3 2025 quarter
- value: 28202000000.000000 USD
- form_type: `10-Q`
- source: `https://www.sec.gov/Archives/edgar/data/320193/000032019325000073/0000320193-25-000073-index.htm`

### Fact 17: operating_income

- evidence_id: `financial_fact:43050`
- label: Operating Income (Loss)
- period: Q3 2025 year-to-date
- value: 100623000000.000000 USD
- form_type: `10-Q`
- source: `https://www.sec.gov/Archives/edgar/data/320193/000032019325000073/0000320193-25-000073-index.htm`

### Fact 18: operating_income

- evidence_id: `financial_fact:43049`
- label: Operating Income (Loss)
- period: Q2 2025 quarter
- value: 29589000000.000000 USD
- form_type: `10-Q`
- source: `https://www.sec.gov/Archives/edgar/data/320193/000032019326000013/0000320193-26-000013-index.htm`

### Fact 19: net_income

- evidence_id: `financial_fact:42880`
- label: Net Income (Loss) Attributable to Parent
- period: Q2 2026 quarter
- value: 29578000000.000000 USD
- form_type: `10-Q`
- source: `https://www.sec.gov/Archives/edgar/data/320193/000032019326000013/0000320193-26-000013-index.htm`

### Fact 20: net_income

- evidence_id: `financial_fact:42879`
- label: Net Income (Loss) Attributable to Parent
- period: Q2 2026 year-to-date
- value: 71675000000.000000 USD
- form_type: `10-Q`
- source: `https://www.sec.gov/Archives/edgar/data/320193/000032019326000013/0000320193-26-000013-index.htm`

### Fact 21: net_income

- evidence_id: `financial_fact:42878`
- label: Net Income (Loss) Attributable to Parent
- period: Q1 2026 quarter
- value: 42097000000.000000 USD
- form_type: `10-Q`
- source: `https://www.sec.gov/Archives/edgar/data/320193/000032019326000006/0000320193-26-000006-index.htm`

### Fact 22: net_income

- evidence_id: `financial_fact:42876`
- label: Net Income (Loss) Attributable to Parent
- period: Q3 2025 quarter
- value: 23434000000.000000 USD
- form_type: `10-Q`
- source: `https://www.sec.gov/Archives/edgar/data/320193/000032019325000073/0000320193-25-000073-index.htm`

### Fact 23: net_income

- evidence_id: `financial_fact:42875`
- label: Net Income (Loss) Attributable to Parent
- period: Q3 2025 year-to-date
- value: 84544000000.000000 USD
- form_type: `10-Q`
- source: `https://www.sec.gov/Archives/edgar/data/320193/000032019325000073/0000320193-25-000073-index.htm`

### Fact 24: net_income

- evidence_id: `financial_fact:42874`
- label: Net Income (Loss) Attributable to Parent
- period: Q2 2025 quarter
- value: 24780000000.000000 USD
- form_type: `10-Q`
- source: `https://www.sec.gov/Archives/edgar/data/320193/000032019326000013/0000320193-26-000013-index.htm`

### Fact 25: operating_cash_flow

- evidence_id: `financial_fact:42946`
- label: Net Cash Provided by (Used in) Operating Activities
- period: Q2 2026 year-to-date
- value: 82627000000.000000 USD
- form_type: `10-Q`
- source: `https://www.sec.gov/Archives/edgar/data/320193/000032019326000013/0000320193-26-000013-index.htm`

### Fact 26: operating_cash_flow

- evidence_id: `financial_fact:42945`
- label: Net Cash Provided by (Used in) Operating Activities
- period: Q1 2026 quarter
- value: 53925000000.000000 USD
- form_type: `10-Q`
- source: `https://www.sec.gov/Archives/edgar/data/320193/000032019326000006/0000320193-26-000006-index.htm`

### Fact 27: operating_cash_flow

- evidence_id: `financial_fact:42943`
- label: Net Cash Provided by (Used in) Operating Activities
- period: Q3 2025 year-to-date
- value: 81754000000.000000 USD
- form_type: `10-Q`
- source: `https://www.sec.gov/Archives/edgar/data/320193/000032019325000073/0000320193-25-000073-index.htm`

### Fact 28: operating_cash_flow

- evidence_id: `financial_fact:42942`
- label: Net Cash Provided by (Used in) Operating Activities
- period: Q2 2025 year-to-date
- value: 53887000000.000000 USD
- form_type: `10-Q`
- source: `https://www.sec.gov/Archives/edgar/data/320193/000032019326000013/0000320193-26-000013-index.htm`

### Fact 29: operating_cash_flow

- evidence_id: `financial_fact:42941`
- label: Net Cash Provided by (Used in) Operating Activities
- period: Q1 2025 quarter
- value: 29935000000.000000 USD
- form_type: `10-Q`
- source: `https://www.sec.gov/Archives/edgar/data/320193/000032019326000006/0000320193-26-000006-index.htm`

### Fact 30: operating_cash_flow

- evidence_id: `financial_fact:42939`
- label: Net Cash Provided by (Used in) Operating Activities
- period: Q3 2024 year-to-date
- value: 91443000000.000000 USD
- form_type: `10-Q`
- source: `https://www.sec.gov/Archives/edgar/data/320193/000032019325000073/0000320193-25-000073-index.htm`

### Fact 31: free_cash_flow

- evidence_id: `financial_fact:43242`
- label: Free Cash Flow
- period: Q2 2026 year-to-date
- value: 78283000000.000000 USD
- form_type: `10-Q`
- source: `https://www.sec.gov/Archives/edgar/data/320193/000032019326000013/0000320193-26-000013-index.htm`

### Fact 32: free_cash_flow

- evidence_id: `financial_fact:43241`
- label: Free Cash Flow
- period: Q1 2026 quarter
- value: 51552000000.000000 USD
- form_type: `10-Q`
- source: `https://www.sec.gov/Archives/edgar/data/320193/000032019326000006/0000320193-26-000006-index.htm`

### Fact 33: free_cash_flow

- evidence_id: `financial_fact:43239`
- label: Free Cash Flow
- period: Q3 2025 year-to-date
- value: 72281000000.000000 USD
- form_type: `10-Q`
- source: `https://www.sec.gov/Archives/edgar/data/320193/000032019325000073/0000320193-25-000073-index.htm`

### Fact 34: free_cash_flow

- evidence_id: `financial_fact:43238`
- label: Free Cash Flow
- period: Q2 2025 year-to-date
- value: 47876000000.000000 USD
- form_type: `10-Q`
- source: `https://www.sec.gov/Archives/edgar/data/320193/000032019326000013/0000320193-26-000013-index.htm`

### Fact 35: free_cash_flow

- evidence_id: `financial_fact:43237`
- label: Free Cash Flow
- period: Q1 2025 quarter
- value: 26995000000.000000 USD
- form_type: `10-Q`
- source: `https://www.sec.gov/Archives/edgar/data/320193/000032019326000006/0000320193-26-000006-index.htm`

### Fact 36: free_cash_flow

- evidence_id: `financial_fact:43235`
- label: Free Cash Flow
- period: Q3 2024 year-to-date
- value: 84904000000.000000 USD
- form_type: `10-Q`
- source: `https://www.sec.gov/Archives/edgar/data/320193/000032019325000073/0000320193-25-000073-index.htm`


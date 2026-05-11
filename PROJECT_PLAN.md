# Equity Research Copilot Project Plan

> Purpose: this file is the canonical project brief. If the assistant loses memory or starts a new session, read this first to understand the product direction, MVP scope, hard requirements, and implementation milestones.

## 1. Project Overview

Equity Research Copilot is an AI-powered company research assistant for US public equities. The user enters a stock ticker and asks company research questions. The system retrieves SEC filings and SEC XBRL financial facts, then produces evidence-grounded answers with citations back to original sources.

The goal is not to build a stock picker. The product should help users research what a company reported, how key financial metrics changed, and where the evidence appears in official filings. The system must avoid unsupported claims, hallucinated citations, and investment recommendations.

First-phase focus:

- SEC filings ingestion
- XBRL financial facts ingestion
- Filing parsing, chunking, embedding, retrieval
- Citation-first AI research Q&A
- Citation validation as a backend system component
- Clear job status, caching, retry, and SEC-compliant request behavior

## 2. Product Positioning

### User Problem

Company research usually requires checking multiple long, messy sources:

- SEC filings such as `10-K`, `10-Q`, and `8-K`
- XBRL financial data
- Management discussion and risk factor sections
- Historical financial values across reporting periods

Generic LLM answers are often not acceptable for finance research because they may:

- Give confident but unsupported statements
- Fail to cite primary sources
- Mix periods incorrectly
- Use stale or inconsistent data
- Accidentally sound like investment advice

### Product Promise

The assistant should answer research questions with traceable evidence. When evidence is missing or weak, it should say so.

Example questions:

- "Apple 最近一个季度收入增长来自哪里？"
- "Tesla 最新 10-K 里提到的主要风险是什么？"
- "NVIDIA 最近两年的毛利率变化原因是什么？"
- "这个公司最新 10-Q 的 management discussion 重点是什么？"

## 3. MVP Scope

### In Scope for v1

- Ticker to CIK/company metadata resolution
- SEC filing metadata ingestion for recent `10-K`, `10-Q`, and `8-K`
- SEC filing HTML download and cache
- Filing parsing into normalized sections and chunks
- Embedding generation and vector retrieval
- SEC XBRL company facts ingestion
- Small, accurate normalized financial metric set
- Citation-first Q&A API and UI
- Backend citation validation before answers are returned
- Job status tracking for ingestion, parsing, embedding, and XBRL loading
- README documentation with disclaimers and data limitations

### Explicitly Out of Scope for v1

- News ingestion
- Watchlist alerts
- Full filing diff
- Email, Slack, or notification delivery
- Full financial statement coverage
- Buy/sell/hold recommendations
- Real-time market data
- Brokerage or trading integration
- Multi-user authentication

### v2 Candidate

Filing Diff is deferred to v2 and should be narrowly scoped:

- Compare latest `10-K` vs prior `10-K`
- Only compare the `Risk Factors` section
- Identify newly added risks, removed risks, more cautious wording, and topic emphasis changes
- Every diff summary must include original-source citations

## 4. Hard Requirements

### Citation-First Behavior

Citation quality is the core product requirement. The model prompt alone is not enough.

The backend must validate citations before returning an answer:

- Every citation must map to a retrieved `document_chunk` or approved XBRL fact.
- The answer cannot cite evidence that was not included in the retrieval context.
- Citation metadata must include SEC URL, form type, filing date, section, and chunk or fact id.
- Important financial numbers must come from normalized XBRL facts when available.
- Unsupported claims should trigger validation failure.
- On validation failure, the system may retry generation once with stricter instructions.
- If validation still fails, return an insufficient-evidence answer instead of a polished unsupported answer.

### SEC Compliance and Robustness

The SEC integration must be respectful and reliable:

- Configure a real SEC `User-Agent` with contact email.
- Apply global SEC request rate limiting, default max `10 requests/second`.
- Cache SEC metadata, XBRL responses, and downloaded filing documents.
- Add exponential backoff retry for transient network or SEC failures.
- Track retryable vs non-retryable failures.
- Store job status and error messages.
- Never silently ignore failed filing downloads, parse failures, or XBRL loading errors.

### XBRL Metrics Scope

XBRL v1 should be small and accurate. Do not try to support every financial statement item.

Normalize only:

- Revenue
- Gross profit
- Operating income
- Net income
- Operating cash flow
- Capital expenditures
- Free cash flow, computed as operating cash flow minus capital expenditures
- Gross margin
- Operating margin
- Net margin

For every metric, preserve:

- Original XBRL taxonomy tag
- Period start and end
- Fiscal year and fiscal period
- Unit
- Value
- Source filing or source fact reference

## 5. Technical Architecture

### Default Stack

- Backend: FastAPI
- Frontend: React
- Database: PostgreSQL
- Vector search: `pgvector` preferred, but keep vector store abstract
- Background jobs: async worker process, exact queue implementation can be chosen during implementation
- AI provider: OpenAI by default, wrapped behind an internal provider interface

### Main Backend Components

- SEC client
  - Ticker to CIK lookup
  - Submissions API fetch
  - Filing download
  - Company facts fetch
  - Rate limit, retry, cache, User-Agent

- Filing parser
  - HTML to normalized text
  - Section extraction
  - Chunking with stable source metadata

- XBRL normalizer
  - Maps a small set of canonical metrics to SEC company facts
  - Handles units, periods, fiscal labels, and source traceability

- Retrieval service
  - Embeds filing chunks
  - Retrieves relevant chunks
  - Adds relevant XBRL facts for metric questions

- Answer service
  - Builds grounded context
  - Calls LLM provider
  - Runs citation validation
  - Returns answer, citations, limitations, and validation status

- Job service
  - Tracks ingestion progress
  - Stores current state and errors
  - Supports manual retry

## 6. Data Model Draft

Core tables:

- `companies`
  - ticker, CIK, name, exchange, SIC/sector if available

- `filings`
  - company id, accession number, form type, filing date, report date, SEC URL, local status

- `filing_sections`
  - filing id, section name, section order, raw text, normalized text

- `document_chunks`
  - filing id, section id, chunk text, token count, SEC URL, accession number, form type, filing date, section, character offsets

- `embeddings`
  - chunk id, model, vector

- `financial_facts`
  - company id, canonical metric key, taxonomy tag, label, period start, period end, fiscal year, fiscal period, unit, value, source accession or source fact id

- `jobs`
  - job type, company id, status, progress, retry count, error message, started at, finished at

- `qa_answers`
  - company id, question, answer, retrieved evidence ids, citation ids, validation status, limitations, created at

## 7. API Draft

Core endpoints:

- `GET /health`
- `GET /companies/search?q=`
- `GET /companies/{ticker}`
- `POST /companies/{ticker}/ingest`
- `GET /companies/{ticker}/jobs`
- `GET /companies/{ticker}/filings`
- `GET /filings/{filing_id}/sections`
- `GET /companies/{ticker}/metrics`
- `POST /research/query`

`POST /research/query` request:

- ticker
- question
- optional form type filter
- optional date range
- optional section filter

`POST /research/query` response:

- answer
- citations
- retrieved evidence ids
- validation status
- limitations
- source coverage summary

## 8. Frontend Requirements

The UI should feel like a professional analyst tool, not a marketing landing page.

Required v1 views:

- Company Search
  - Enter ticker
  - Show company metadata and ingestion state

- Job Status
  - Show ingestion, parsing, embedding, XBRL loading states
  - Show failures and retry availability

- Filing Explorer
  - Show recent `10-K`, `10-Q`, and `8-K`
  - Show parsed sections and source links

- Financial Metrics
  - Show small normalized metric set
  - Show period, unit, source, and calculation notes

- Cited Q&A
  - Ask natural-language research questions
  - Show answer with citations
  - Show citation details and validation status
  - Show limitations when evidence is insufficient

## 9. Milestones

### Milestone 1: Project Foundation

Goal: establish the basic full-stack app and local development workflow.

Tasks:

- Create FastAPI backend structure.
- Create React frontend structure.
- Add PostgreSQL setup and migrations.
- Add configuration for SEC User-Agent and OpenAI API key.
- Add basic logging and health checks.
- Add initial `jobs` model and job status API.

Acceptance criteria:

- Backend starts locally.
- Frontend starts locally.
- Backend connects to database.
- Frontend can call `/health`.
- Required environment variables are documented.

### Milestone 2: SEC Ingestion

Goal: fetch and store company and filing metadata from SEC.

Tasks:

- Implement ticker to CIK lookup.
- Fetch SEC submissions.
- Store recent `10-K`, `10-Q`, and `8-K` metadata.
- Add SEC request rate limiter.
- Add SEC User-Agent configuration.
- Add cache for SEC responses.
- Add retry and failure handling.
- Update job status during ingestion.

Acceptance criteria:

- User can ingest `AAPL`, `TSLA`, and `NVDA`.
- Recent filing metadata is stored with SEC links.
- SEC failures appear in job status.
- Re-running ingestion uses cache where possible.

### Milestone 3: Filing Download, Parsing, and Chunking

Goal: turn filings into searchable, citeable text chunks.

Tasks:

- Download filing HTML.
- Cache raw filing documents.
- Parse HTML to clean text.
- Extract major sections where possible.
- Create chunks with source metadata.
- Store chunks in database.
- Build Filing Explorer UI.

Acceptance criteria:

- Latest `10-K` and `10-Q` for demo tickers can be parsed.
- Chunks include accession number, form type, filing date, section, SEC URL, and offsets.
- UI can display filing sections and source links.

### Milestone 4: XBRL Metrics

Goal: load a small, reliable financial metric set from SEC XBRL company facts.

Tasks:

- Fetch SEC company facts.
- Normalize the v1 metric set only.
- Store source tags, periods, units, values, and source references.
- Compute free cash flow and margin metrics.
- Build financial metrics UI.

Acceptance criteria:

- Revenue, gross profit, operating income, net income, operating cash flow, capex, free cash flow, and margins display for demo tickers when available.
- Metrics show period and source traceability.
- Missing metrics are shown as unavailable rather than guessed.

### Milestone 5: Embeddings and Retrieval

Goal: retrieve relevant filing evidence for user questions.

Tasks:

- Add embedding provider interface.
- Generate embeddings for filing chunks.
- Store vectors in `pgvector` or equivalent vector store.
- Implement semantic retrieval.
- Add metric-aware retrieval that can include XBRL facts.
- Add developer/debug view of retrieved evidence.

Acceptance criteria:

- A question retrieves relevant filing chunks.
- Metric-related questions retrieve relevant XBRL facts.
- Retrieved evidence ids are available to the answer service and validator.

### Milestone 6: Citation-Grounded Q&A and Validation

Goal: generate answers that are evidence-grounded and system-validated.

Tasks:

- Implement `/research/query`.
- Build prompt using retrieved chunks and facts.
- Generate answer with citation markers.
- Implement citation validation.
- Retry once on validation failure.
- Return insufficient-evidence response if validation still fails.
- Build Cited Q&A UI.

Acceptance criteria:

- Answers include citations that map to retrieved chunks or facts.
- Invalid citations are rejected.
- Unsupported claims are blocked or converted into limitations.
- Questions about latest risk factors cite latest filing risk factor chunks.
- Financial number claims cite XBRL facts when available.

### Milestone 7: Documentation and Interview Readiness

Goal: make the project understandable, demoable, and interview-ready.

Tasks:

- Update README with setup instructions.
- Add no-investment-advice disclaimer.
- Document citation-first design.
- Document SEC rate limit, User-Agent, cache, retry, and job status.
- Document XBRL metric limitations.
- Document look-ahead bias and public data limitations.
- Add demo script for `AAPL`, `TSLA`, and `NVDA`.
- Add concise architecture explanation.

Acceptance criteria:

- A new reader can understand the project from README and this plan.
- A demo can be run end-to-end.
- Interview talking points are documented.

## 10. Test Plan

### Unit Tests

- Ticker to CIK resolution
- SEC response caching
- SEC retry behavior
- SEC rate limiter behavior
- Filing metadata parsing
- Filing section extraction
- Chunk metadata creation
- XBRL metric normalization
- Free cash flow calculation
- Margin calculation
- Citation validator success cases
- Citation validator failure cases

### Integration Tests

- Ingest `AAPL` end to end.
- Parse latest `10-K` and create chunks.
- Load selected XBRL metrics.
- Generate embeddings for chunks.
- Ask a research question and validate citations.
- Force invalid citations and verify rejection.
- Simulate SEC failure and verify job status.

### Evaluation Questions

- "Apple 最近一个季度收入增长来自哪里？"
- "Tesla 最新 10-K 里提到的主要风险是什么？"
- "NVIDIA 最近两年的毛利率变化原因是什么？"
- "这个公司最新 10-Q 的 management discussion 重点是什么？"

Expected behavior:

- The system cites retrieved evidence.
- The system refuses or qualifies unsupported answers.
- The system distinguishes filing prose from XBRL financial facts.
- The system does not produce investment recommendations.

## 11. README Requirements

README must clearly state:

- This project is not investment advice.
- The system is citation-first and evidence-grounded.
- AI answers may still be incomplete or wrong if source data is incomplete.
- SEC and XBRL data has reporting delays and taxonomy inconsistencies.
- Users must avoid look-ahead bias when interpreting historical financial or price-related data.
- Free/public data sources have coverage and reliability limitations.
- The project prioritizes traceability over confident-sounding answers.

## 12. Finance and AI Pitfalls to Address

The project should be designed around these risks:

- Hallucinated citations
- Unsupported financial claims
- Mixing annual and quarterly periods
- Mixing fiscal and calendar periods
- Using restated or later data as if it were available historically
- Treating SEC filing date and report period as the same thing
- Overclaiming from incomplete XBRL facts
- Giving investment advice instead of research assistance
- Ignoring SEC request limits or User-Agent requirements

## 13. Current Repository State

As of this plan:

- `README.md` is minimal.
- `DESIGN.md` contains the initial project motivation.
- Implementation has not started.
- This file should guide the first real engineering pass.


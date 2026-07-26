# Equity Research Copilot

[中文版本](./README.zh-CN.md)

Equity Research Copilot is a full-stack research assistant for US public equities. The backend currently supports SEC company and filing metadata ingestion, SEC filing HTML download, `sec2md` parsing, section extraction, citation-ready chunk storage for recent `10-K`, `10-Q`, and `8-K` filings, normalized XBRL financial metrics from SEC company facts, chunk embeddings, semantic retrieval, metric-aware retrieval, cited answer generation, and auditable research-run packaging.

This project is for research assistance only. It is not investment advice.

## Current Scope

Implemented:

- FastAPI backend and React frontend foundation.
- PostgreSQL setup with Alembic migrations and pgvector support.
- Health checks, request logging, and environment configuration.
- Job status tracking API.
- SEC ticker to CIK/company lookup.
- SEC submissions ingestion for recent `10-K`, `10-Q`, and `8-K` filing metadata.
- SEC response cache with optional refresh bypass.
- SEC request User-Agent, rate limiting, retry, and failure handling.
- Filing HTML download through the project SEC client.
- Raw and annotated filing document cache.
- `sec2md` parsing for filing sections and page-aware chunks.
- Filing Explorer UI for metadata ingestion, filing parsing, sections, chunks, and source links.
- XBRL company facts loading for the v1 financial metric set.
- Computed free cash flow and margin metrics with source traceability.
- Metrics UI with unavailable states for missing facts.
- Embedding provider interface and batch chunk embedding generation with versioned embedding inputs.
- Dense retrieval, lexical retrieval, XBRL fact retrieval, rule-based query planning, optional LLM planner fallback, RRF fusion, metadata reranking, and retrieval trace output.
- Final evidence pack selection with role-based chunk groups, selected evidence spans, metric comparisons, and stable evidence ids.
- Developer/debug retrieval API and frontend Research view.
- Answer evidence context contract (`answer_evidence_context.v1`) used by answer generation, citation validation, and research-run responses.
- Auditable research-run API and minimal frontend trace viewer for planner, agent steps, evidence, validation, and diagnostics.
- Retrieval dump and gold-eval utilities.
- Company, filing, parsing, metrics, embedding, retrieval, and job read APIs.

Not implemented yet:

- Broader claim-level support validation beyond citation ID checks.
- Production hardening for the research-run workflow and trace viewer.
- Production retrieval optimizations such as MMR diversity, neighbor expansion, learned reranking, and broader eval coverage. HNSW indexing is implemented; recall tuning across larger corpora remains open.

## Prerequisites

- Python 3.11+
- Node.js 20.19+ or 22.12+
- Docker Desktop with Docker Compose

## Required Environment Variables

Backend environment variables are loaded from `backend/.env`. Start from the example file:

macOS/Linux:

```bash
cp backend/.env.example backend/.env
```

Windows PowerShell:

```powershell
Copy-Item backend/.env.example backend/.env
```

Required values:

| Variable | Required | Description |
| --- | --- | --- |
| `DATABASE_URL` | No | PostgreSQL connection URL. Defaults to the local Docker Compose database. |
| `SEC_USER_AGENT` | Yes | User-Agent sent to SEC APIs. Include app name and contact email. |
| `SEC_RATE_LIMIT_PER_SECOND` | No | SEC request limit. Defaults to `10`, the maximum allowed by the app configuration. |
| `SEC_CACHE_TTL_SECONDS` | No | SEC JSON response cache TTL. Defaults to `86400` seconds. |
| `OPENAI_API_KEY` | Yes for embeddings and LLM planning | OpenAI API key used by the default embedding provider and by LLM-first query planning. Retrieval can still degrade to lexical and XBRL facts without dense embeddings, and query planning falls back to broad text retrieval if the LLM is unavailable. |
| `EMBEDDING_PROVIDER` | No | Embedding provider. Defaults to `openai`. |
| `EMBEDDING_MODEL` | No | Embedding model. Defaults to `text-embedding-3-small`. |
| `EMBEDDING_DIMENSIONS` | No | Embedding vector dimensions. Defaults to `1536`. |
| `EMBEDDING_INPUT_VERSION` | No | Version for the document embedding input template. Defaults to `v1`. |
| `VECTOR_SEARCH_MODE` | No | Vector search profile: `hnsw` (default) uses the pgvector HNSW index from migration 0008, `exact` forces exact scans, `auto` lets the Postgres planner decide. |
| `HNSW_EF_SEARCH` | No | Transaction-local `hnsw.ef_search` budget used when `VECTOR_SEARCH_MODE=hnsw`. Defaults to `80`. |
| `RETRIEVAL_DENSE_CANDIDATES` | No | Dense candidate budget. Defaults to `40`. |
| `RETRIEVAL_LEXICAL_CANDIDATES` | No | Lexical candidate budget. Defaults to `40`. |
| `RETRIEVAL_FACT_CANDIDATES` | No | XBRL fact candidate budget. Defaults to `20`. |
| `RETRIEVAL_TOP_K` | No | Final chunk evidence count before evidence-pack selection. Defaults to `10`. |
| `QUERY_PLANNER_MODE` | No | Query planner mode. Defaults to `llm`. Legacy values `rule_only` and `rule_with_llm_fallback` are accepted for compatibility; `rule_with_llm_fallback` now uses the LLM-first planner. |
| `QUERY_PLANNER_LLM_MODEL` | No | Model used by the LLM planner. Defaults to `gpt-4o-mini`. |
| `QUERY_PLANNER_LLM_TIMEOUT_SECONDS` | No | Timeout for the LLM planner call. Defaults to `20`. |
| `QUERY_PLANNER_LLM_MAX_RETRIES` | No | OpenAI SDK retry count for planner calls. Defaults to `0` so local planner tests fail fast instead of waiting through retries. |
| `ANSWER_LLM_MAX_OUTPUT_TOKENS` | No | Upper bound on answer generation output tokens to cap tail latency. Defaults to `900`. |

Example:

```env
DATABASE_URL="postgresql+psycopg://equity_research:equity_research_password@localhost:5432/equity_research_copilot"
SEC_USER_AGENT="Equity Research Copilot/0.1 (contact: your-email@example.com)"
SEC_RATE_LIMIT_PER_SECOND=10
SEC_CACHE_TTL_SECONDS=86400
OPENAI_API_KEY=""
EMBEDDING_PROVIDER="openai"
EMBEDDING_MODEL="text-embedding-3-small"
EMBEDDING_DIMENSIONS=1536
EMBEDDING_INPUT_VERSION="v1"
VECTOR_SEARCH_MODE="hnsw"
HNSW_EF_SEARCH=80
RETRIEVAL_DENSE_CANDIDATES=40
RETRIEVAL_LEXICAL_CANDIDATES=40
RETRIEVAL_FACT_CANDIDATES=20
RETRIEVAL_TOP_K=10
QUERY_PLANNER_MODE="llm"
QUERY_PLANNER_LLM_MODEL="gpt-4o-mini"
QUERY_PLANNER_LLM_TIMEOUT_SECONDS=20
QUERY_PLANNER_LLM_MAX_RETRIES=0
```

## Local Development

Start PostgreSQL from the repository root:

```bash
docker compose -f compose.yaml up -d postgres
```

Install backend dependencies and start the API.

macOS/Linux:

```bash
cd backend
python3 -m venv .venv
./.venv/bin/python -m pip install -e ".[dev]"
./.venv/bin/alembic upgrade head
./.venv/bin/uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

Windows PowerShell:

```powershell
Set-Location backend
py -3 -m venv .venv
.\.venv\Scripts\python -m pip install -e .[dev]
.\.venv\Scripts\alembic upgrade head
.\.venv\Scripts\uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

If the Windows Python launcher is unavailable, replace `py -3` with `python`.

In another terminal, install and start the frontend.

macOS/Linux:

```bash
cd frontend
npm install
npm run dev
```

Windows PowerShell:

```powershell
Set-Location frontend
npm install
npm run dev
```

The frontend dev server proxies `/health`, `/companies`, `/filings`, `/jobs`, and `/research` to the backend at `http://127.0.0.1:8000`.

## SEC Ingestion

Start the backend first, then trigger ingestion from another terminal.

Fetch fresh SEC metadata for Apple. Filing metadata ingestion bypasses the SEC response cache by default so newly accepted `10-K`, `10-Q`, and `8-K` filings are picked up promptly.

macOS/Linux:

```bash
JOB_ID=$(curl -s -X POST "http://127.0.0.1:8000/companies/AAPL/ingest" | python3 -c 'import json, sys; print(json.load(sys.stdin)["id"])')
curl "http://127.0.0.1:8000/jobs/$JOB_ID"
curl "http://127.0.0.1:8000/companies/AAPL"
curl "http://127.0.0.1:8000/companies/AAPL/filings"
curl "http://127.0.0.1:8000/companies/AAPL/filings?form_type=10-K"
```

Windows PowerShell:

```powershell
$job = Invoke-RestMethod -Method Post "http://127.0.0.1:8000/companies/AAPL/ingest"
Invoke-RestMethod "http://127.0.0.1:8000/jobs/$($job.id)"
Invoke-RestMethod "http://127.0.0.1:8000/companies/AAPL"
Invoke-RestMethod "http://127.0.0.1:8000/companies/AAPL/filings"
Invoke-RestMethod "http://127.0.0.1:8000/companies/AAPL/filings?form_type=10-K"
```

Run the demo tickers:

macOS/Linux:

```bash
curl -X POST "http://127.0.0.1:8000/companies/AAPL/ingest?refresh=true"
curl -X POST "http://127.0.0.1:8000/companies/TSLA/ingest?refresh=true"
curl -X POST "http://127.0.0.1:8000/companies/NVDA/ingest?refresh=true"
```

Windows PowerShell:

```powershell
Invoke-RestMethod -Method Post "http://127.0.0.1:8000/companies/AAPL/ingest?refresh=true"
Invoke-RestMethod -Method Post "http://127.0.0.1:8000/companies/TSLA/ingest?refresh=true"
Invoke-RestMethod -Method Post "http://127.0.0.1:8000/companies/NVDA/ingest?refresh=true"
```

Pass `refresh=false` only when you intentionally want to reuse unexpired SEC response cache.

To inspect cached SEC responses directly:

```bash
docker exec -it equity_research_copilot_postgres psql -U equity_research -d equity_research_copilot
```

```sql
SELECT id, url, status_code, fetched_at, expires_at
FROM sec_response_cache
ORDER BY fetched_at DESC;
```

## Filing Parsing

Milestone 3 uses [`sec2md`](https://github.com/lucasastorian/sec2md) to convert filing HTML into clean markdown pages, extracted sections, and page-aware chunks. The app still downloads SEC documents through its own SEC client so User-Agent, retry, rate limiting, and failure handling remain centralized.

Parse a stored filing after metadata ingestion.

macOS/Linux:

```bash
FILING_ID=$(curl -s "http://127.0.0.1:8000/companies/AAPL/filings?form_type=10-K&limit=1" | python3 -c 'import json, sys; print(json.load(sys.stdin)[0]["id"])')
PARSE_JOB_ID=$(curl -s -X POST "http://127.0.0.1:8000/filings/$FILING_ID/parse" | python3 -c 'import json, sys; print(json.load(sys.stdin)["id"])')
curl "http://127.0.0.1:8000/jobs/$PARSE_JOB_ID"
curl "http://127.0.0.1:8000/filings/$FILING_ID/sections"
curl "http://127.0.0.1:8000/filings/$FILING_ID/chunks?limit=10"
```

Windows PowerShell:

```powershell
$filings = Invoke-RestMethod "http://127.0.0.1:8000/companies/AAPL/filings?form_type=10-K&limit=1"
$parseJob = Invoke-RestMethod -Method Post "http://127.0.0.1:8000/filings/$($filings[0].id)/parse"
Invoke-RestMethod "http://127.0.0.1:8000/jobs/$($parseJob.id)"
Invoke-RestMethod "http://127.0.0.1:8000/filings/$($filings[0].id)/sections"
Invoke-RestMethod "http://127.0.0.1:8000/filings/$($filings[0].id)/chunks?limit=10"
```

Force a fresh filing HTML download and re-parse:

macOS/Linux:

```bash
curl -X POST "http://127.0.0.1:8000/filings/$FILING_ID/parse?refresh=true"
```

Windows PowerShell:

```powershell
Invoke-RestMethod -Method Post "http://127.0.0.1:8000/filings/$($filings[0].id)/parse?refresh=true"
```

## Retrieval and Evidence

Milestone 5 retrieval is implemented. The system can embed parsed filing chunks, retrieve relevant filing evidence for a user question, include metric-aware XBRL facts and comparisons, and return stable evidence ids for cited answer generation and citation ID validation.

Generate embeddings after filings are parsed:

macOS/Linux:

```bash
EMBED_JOB_ID=$(curl -s -X POST "http://127.0.0.1:8000/companies/AAPL/embeddings/generate" | python3 -c 'import json, sys; print(json.load(sys.stdin)["id"])')
curl "http://127.0.0.1:8000/jobs/$EMBED_JOB_ID"
```

Windows PowerShell:

```powershell
$embedJob = Invoke-RestMethod -Method Post "http://127.0.0.1:8000/companies/AAPL/embeddings/generate"
Invoke-RestMethod "http://127.0.0.1:8000/jobs/$($embedJob.id)"
```

Load XBRL metrics for metric-related questions:

macOS/Linux:

```bash
METRICS_JOB_ID=$(curl -s -X POST "http://127.0.0.1:8000/companies/AAPL/metrics/load?refresh=false" | python3 -c 'import json, sys; print(json.load(sys.stdin)["id"])')
curl "http://127.0.0.1:8000/jobs/$METRICS_JOB_ID"
```

Windows PowerShell:

```powershell
$metricsJob = Invoke-RestMethod -Method Post "http://127.0.0.1:8000/companies/AAPL/metrics/load?refresh=false"
Invoke-RestMethod "http://127.0.0.1:8000/jobs/$($metricsJob.id)"
```

Call the retrieval API:

macOS/Linux:

```bash
curl -s -X POST "http://127.0.0.1:8000/research/retrieve?view=analysis" \
  -H "Content-Type: application/json" \
  -d '{"ticker":"AAPL","question":"What drove Apple revenue growth?"}'
```

Windows PowerShell:

```powershell
Invoke-RestMethod -Method Post "http://127.0.0.1:8000/research/retrieve?view=analysis" `
  -ContentType "application/json" `
  -Body '{"ticker":"AAPL","question":"What drove Apple revenue growth?"}'
```

`POST /research/runs` returns the final cited answer plus an auditable `research_run.v1` contract containing planner output, agent/tool steps, normalized evidence, validation status, limitations, and retrieval diagnostics. Each run is persisted to the `research_runs` table; `GET /research/runs/{run_id}` returns the stored contract and `GET /research/runs?ticker=&limit=` lists recent run summaries.

```bash
curl -X POST "http://127.0.0.1:8000/research/runs" \
  -H "Content-Type: application/json" \
  -d '{"ticker":"AAPL","question":"What drove revenue growth last quarter?"}'
```

The research-run response includes `answer`, `validation`, `limitations`, `plan`, `steps`, normalized `evidence`, and `diagnostics`. Each step carries evidence ids that point into the normalized evidence list, and `diagnostics` preserves planner summary, retrieval configuration, source coverage, and score-breakdown details when available.

`POST /research/retrieve?view=analysis` remains the lower-level developer/debug retrieval endpoint. Its compact response includes `retrieval_plan`, `source_coverage_summary`, `final_evidence_pack`, `top_chunks`, `top_facts`, `metric_comparisons`, and `analysis_trace` for terminal inspection. The full retrieval response and retrieval dump utilities expose the raw `retrieved_chunks`, `retrieved_facts`, and `retrieval_trace` payloads when deeper debugging is needed.

`final_evidence_pack` groups selected evidence into primary financial statement chunks, MD&A explanation chunks, segment or product breakdown chunks, annual context chunks, metric comparisons, and selected evidence spans. Spans are short excerpts selected from retrieved chunks because they are the most directly useful text for answering the question; they retain their source chunk evidence id, page metadata, SEC URL, and selection reasons.

Dense retrieval degrades gracefully when embeddings are missing or unavailable; lexical retrieval and XBRL fact retrieval still run when possible. The frontend Research view calls `/research/runs` and shows the cited answer, validation result, agent step timeline, selected-step evidence, and retrieval diagnostics. Raw retrieval details remain available through the full `/research/retrieve` response and retrieval dump utilities.

## Evaluation Utilities

Run the query planner eval from the repository root:

macOS/Linux:

```bash
PYTHONPATH=backend backend/.venv/bin/python -m app.evals.query_planner_eval
```

Windows PowerShell:

```powershell
$env:PYTHONPATH = "backend"
backend\.venv\Scripts\python -m app.evals.query_planner_eval
```

Dump retrieval diagnostics for a question:

macOS/Linux:

```bash
PYTHONPATH=backend backend/.venv/bin/python -m app.evals.retrieval_dump AAPL "What drove Apple revenue growth?"
```

Windows PowerShell:

```powershell
$env:PYTHONPATH = "backend"
backend\.venv\Scripts\python -m app.evals.retrieval_dump AAPL "What drove Apple revenue growth?"
```

Run the retrieval gold eval:

macOS/Linux:

```bash
PYTHONPATH=backend backend/.venv/bin/python -m app.evals.retrieval_gold_eval
```

Windows PowerShell:

```powershell
$env:PYTHONPATH = "backend"
backend\.venv\Scripts\python -m app.evals.retrieval_gold_eval
```

The current gold eval seed set lives at `backend/evals/retrieval_gold_eval.json`. It is intentionally small and should be refreshed when chunking, SEC data, or local fixture filings change.

Run the end-to-end answer quality eval (validation status, citation counts, claim-level citation coverage, content patterns, and latency budgets per case):

macOS/Linux:

```bash
PYTHONPATH=backend backend/.venv/bin/python -m app.evals.answer_eval
```

Windows PowerShell:

```powershell
$env:PYTHONPATH = "backend"
backend\.venv\Scripts\python -m app.evals.answer_eval
```

The answer eval set lives at `backend/evals/answer_gold_eval.json` and runs the full `/research/runs` pipeline, so it requires a seeded database and an OpenAI API key.

## API Endpoints

- `GET /health`
- `GET /jobs`
- `GET /jobs/{job_id}`
- `GET /companies/search?q=...`
- `GET /companies/{ticker}`
- `POST /companies/{ticker}/ingest?refresh=true`
- `POST /companies/{ticker}/embeddings/generate?refresh=false`
- `POST /companies/{ticker}/metrics/load?refresh=false`
- `GET /companies/{ticker}/metrics?metric_key=&limit=`
- `GET /companies/{ticker}/jobs`
- `GET /companies/{ticker}/filings?form_type=&limit=`
- `POST /filings/{filing_id}/parse?refresh=false`
- `GET /filings/{filing_id}/sections`
- `GET /filings/{filing_id}/sections/{section_id}`
- `GET /filings/{filing_id}/chunks?section_id=&limit=`
- `GET /filings/{filing_id}/chunks/{chunk_id}/source`
- `POST /research/retrieve`
- `POST /research/retrieve?view=analysis`
- `POST /research/plan`
- `POST /research/query`
- `POST /research/runs`
- `GET /research/runs?ticker=&limit=`
- `GET /research/runs/{run_id}`

## Verification

Backend tests.

macOS/Linux:

```bash
cd backend
./.venv/bin/python -m pytest
```

Windows PowerShell:

```powershell
Set-Location backend
.\.venv\Scripts\python -m pytest
```

Frontend build.

macOS/Linux:

```bash
cd frontend
npm run build
```

Windows PowerShell:

```powershell
Set-Location frontend
npm run build
```

Health check.

macOS/Linux:

```bash
curl http://127.0.0.1:8000/health
```

Windows PowerShell:

```powershell
Invoke-RestMethod http://127.0.0.1:8000/health
```

## Data Limitations

- The system currently stores SEC filing metadata, raw filing HTML, parsed section markdown, document chunks, chunk embeddings, XBRL facts, computed metrics, and retrieval evidence diagnostics.
- SEC data can be delayed, amended, incomplete, or inconsistent across forms and companies.
- Filing date and report date are different concepts and should not be treated as interchangeable.
- M3 parses the primary SEC HTML document only; `8-K` exhibit files are not downloaded as separate documents yet.
- `sec2md` only supports HTML input. PDF or non-HTML primary documents are marked as parse failures.
- Chunk highlighted-source pages are generated dynamically from stored annotated HTML and chunk element ids.
- XBRL metrics use a conservative US-GAAP tag mapping. Missing metrics are shown as unavailable rather than guessed.
- The research workflow can generate final cited natural-language answers and validate citation IDs against the allowed evidence set.
- Query planning is LLM-first. If the LLM is unavailable, the backend falls back to broad text retrieval instead of using brittle keyword slot rules.
- HNSW auto mode, learned reranking, larger eval coverage, deeper claim-level support validation, and production hardening remain future work.

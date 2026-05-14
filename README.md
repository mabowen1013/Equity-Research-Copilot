# Equity Research Copilot

Equity Research Copilot is a full-stack research assistant for US public equities. The backend currently supports SEC company and filing metadata ingestion: ticker to CIK lookup, SEC submissions fetch, recent `10-K` / `10-Q` / `8-K` metadata storage, SEC response caching, rate limiting, retry handling, and ingestion job status tracking.

This project is for research assistance only. It is not investment advice.

## Current Scope

Implemented:

- FastAPI backend and React frontend foundation.
- PostgreSQL setup with Alembic migrations.
- Health checks, request logging, and environment configuration.
- Job status tracking API.
- SEC ticker to CIK/company lookup.
- SEC submissions ingestion for recent `10-K`, `10-Q`, and `8-K` filing metadata.
- SEC response cache with optional refresh bypass.
- SEC request User-Agent, rate limiting, retry, and failure handling.
- Company, filing, and ingestion job read APIs.

Not implemented yet:

- Filing HTML download and raw document cache.
- Filing parsing, section extraction, and chunking.
- XBRL company facts and normalized financial metrics.
- Embeddings, retrieval, citation-grounded Q&A, and citation validation.
- Frontend views for ingestion, filing explorer, metrics, or Q&A.

## Prerequisites

- Python 3.11+
- Node.js 20.19+ or 22.12+
- Docker Desktop with Docker Compose

## Required Environment Variables

Backend environment variables are loaded from `backend/.env`. Start from the example file:

macOS / Linux:

```zsh
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
| `OPENAI_API_KEY` | No | OpenAI API key for later AI features. Can be left empty until those features are used. |

Example:

```env
DATABASE_URL="postgresql+psycopg://equity_research:equity_research_password@localhost:5432/equity_research_copilot"
SEC_USER_AGENT="Equity Research Copilot/0.1 (contact: your-email@example.com)"
SEC_RATE_LIMIT_PER_SECOND=10
SEC_CACHE_TTL_SECONDS=86400
OPENAI_API_KEY=""
```

## Local Development

Start PostgreSQL:

```zsh
docker compose -f compose.yaml up -d postgres
```

Install backend dependencies:

macOS / Linux:

```zsh
cd backend
python3 -m venv .venv
.venv/bin/python -m pip install -e '.[dev]'
```

Windows PowerShell:

```powershell
Set-Location backend
python -m venv .venv
.\.venv\Scripts\python -m pip install -e ".[dev]"
```

Run database migrations:

macOS / Linux:

```zsh
.venv/bin/alembic upgrade head
```

Windows PowerShell:

```powershell
.\.venv\Scripts\alembic upgrade head
```

Start the backend API:

macOS / Linux:

```zsh
.venv/bin/uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

Windows PowerShell:

```powershell
.\.venv\Scripts\uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

In another terminal, install and start the frontend:

macOS / Linux:

```zsh
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

The frontend dev server proxies `/health` to the backend at `http://127.0.0.1:8000`.

## SEC Ingestion

Start the backend first, then trigger ingestion from another terminal.

Force a fresh SEC fetch for Apple:

macOS / Linux:

```zsh
job_response=$(curl -sS -X POST "http://127.0.0.1:8000/companies/AAPL/ingest?refresh=true")
printf '%s\n' "$job_response"
job_id=$(printf '%s' "$job_response" | python3 -c 'import json, sys; print(json.load(sys.stdin)["id"])')
```

Windows PowerShell:

```powershell
$job = Invoke-RestMethod -Method Post "http://127.0.0.1:8000/companies/AAPL/ingest?refresh=true"
$job
```

Check the job status:

macOS / Linux:

```zsh
curl -sS "http://127.0.0.1:8000/jobs/$job_id"
```

Windows PowerShell:

```powershell
Invoke-RestMethod "http://127.0.0.1:8000/jobs/$($job.id)"
```

Read stored company metadata and filings:

macOS / Linux:

```zsh
curl -sS "http://127.0.0.1:8000/companies/AAPL"
curl -sS "http://127.0.0.1:8000/companies/AAPL/filings"
curl -sS "http://127.0.0.1:8000/companies/AAPL/filings?form_type=10-K"
```

Windows PowerShell:

```powershell
Invoke-RestMethod "http://127.0.0.1:8000/companies/AAPL"
Invoke-RestMethod "http://127.0.0.1:8000/companies/AAPL/filings"
Invoke-RestMethod "http://127.0.0.1:8000/companies/AAPL/filings?form_type=10-K"
```

Run the demo tickers:

macOS / Linux:

```zsh
curl -sS -X POST "http://127.0.0.1:8000/companies/AAPL/ingest?refresh=true"
curl -sS -X POST "http://127.0.0.1:8000/companies/TSLA/ingest?refresh=true"
curl -sS -X POST "http://127.0.0.1:8000/companies/NVDA/ingest?refresh=true"
```

Windows PowerShell:

```powershell
Invoke-RestMethod -Method Post "http://127.0.0.1:8000/companies/AAPL/ingest?refresh=true"
Invoke-RestMethod -Method Post "http://127.0.0.1:8000/companies/TSLA/ingest?refresh=true"
Invoke-RestMethod -Method Post "http://127.0.0.1:8000/companies/NVDA/ingest?refresh=true"
```

Omit `refresh=true` to reuse unexpired SEC response cache where possible:

macOS / Linux:

```zsh
curl -sS -X POST "http://127.0.0.1:8000/companies/AAPL/ingest"
```

Windows PowerShell:

```powershell
Invoke-RestMethod -Method Post "http://127.0.0.1:8000/companies/AAPL/ingest"
```

To inspect cached SEC responses directly:

```powershell
docker exec -it equity_research_copilot_postgres psql -U equity_research -d equity_research_copilot
```

```sql
SELECT id, url, status_code, fetched_at, expires_at
FROM sec_response_cache
ORDER BY fetched_at DESC;
```

## API Endpoints

- `GET /health`
- `GET /jobs`
- `GET /jobs/{job_id}`
- `GET /companies/search?q=...`
- `GET /companies/{ticker}`
- `POST /companies/{ticker}/ingest?refresh=false`
- `GET /companies/{ticker}/jobs`
- `GET /companies/{ticker}/filings?form_type=&limit=`

## Verification

Backend tests:

macOS / Linux:

```zsh
cd backend
.venv/bin/python -m pytest
```

Windows PowerShell:

```powershell
Set-Location backend
.\.venv\Scripts\python -m pytest
```

Frontend build:

macOS / Linux:

```zsh
cd frontend
npm run build
```

Windows PowerShell:

```powershell
Set-Location frontend
npm run build
```

Health check:

macOS / Linux:

```zsh
curl http://127.0.0.1:8000/health
```

Windows PowerShell:

```powershell
Invoke-RestMethod http://127.0.0.1:8000/health
```

## Data Limitations

- The system currently stores SEC filing metadata and source links, not filing text.
- SEC data can be delayed, amended, incomplete, or inconsistent across forms and companies.
- Filing date and report date are different concepts and should not be treated as interchangeable.
- Later milestones will add filing parsing, XBRL metrics, retrieval, citations, and answer validation.

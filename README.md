# Equity Research Copilot

Equity Research Copilot is a full-stack research assistant for US public equities. The backend currently supports SEC company and filing metadata ingestion, SEC filing HTML download, `sec2md` parsing, section extraction, and citation-ready chunk storage for recent `10-K`, `10-Q`, and `8-K` filings.

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
- Filing HTML download through the project SEC client.
- Raw and annotated filing document cache.
- `sec2md` parsing for filing sections and chunks.
- Filing Explorer UI for metadata ingestion, filing parsing, sections, chunks, and source links.
- Company, filing, parsing, and job read APIs.

Not implemented yet:

- XBRL company facts and normalized financial metrics.
- Embeddings, retrieval, citation-grounded Q&A, and citation validation.
- Frontend views for metrics or Q&A.

## Prerequisites

- Python 3.11+
- Node.js 20.19+ or 22.12+
- Docker Desktop with Docker Compose

## Required Environment Variables

Backend environment variables are loaded from `backend/.env`. Start from the example file:

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

```powershell
docker compose -f compose.yaml up -d postgres
```

Install backend dependencies:

```powershell
Set-Location backend
python -m venv .venv
.\.venv\Scripts\python -m pip install -e .[dev]
```

Run database migrations:

```powershell
.\.venv\Scripts\alembic upgrade head
```

Start the backend API:

```powershell
.\.venv\Scripts\uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

In another terminal, install and start the frontend:

```powershell
Set-Location frontend
npm install
npm run dev
```

The frontend dev server proxies `/health`, `/companies`, `/filings`, and `/jobs` to the backend at `http://127.0.0.1:8000`.

## SEC Ingestion

Start the backend first, then trigger ingestion from another PowerShell session.

Force a fresh SEC fetch for Apple:

```powershell
$job = Invoke-RestMethod -Method Post "http://127.0.0.1:8000/companies/AAPL/ingest?refresh=true"
$job
```

Check the job status:

```powershell
Invoke-RestMethod "http://127.0.0.1:8000/jobs/$($job.id)"
```

Read stored company metadata and filings:

```powershell
Invoke-RestMethod "http://127.0.0.1:8000/companies/AAPL"
Invoke-RestMethod "http://127.0.0.1:8000/companies/AAPL/filings"
Invoke-RestMethod "http://127.0.0.1:8000/companies/AAPL/filings?form_type=10-K"
```

Run the demo tickers:

```powershell
Invoke-RestMethod -Method Post "http://127.0.0.1:8000/companies/AAPL/ingest?refresh=true"
Invoke-RestMethod -Method Post "http://127.0.0.1:8000/companies/TSLA/ingest?refresh=true"
Invoke-RestMethod -Method Post "http://127.0.0.1:8000/companies/NVDA/ingest?refresh=true"
```

Omit `refresh=true` to reuse unexpired SEC response cache where possible:

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

## Filing Parsing

Milestone 3 uses [`sec2md`](https://github.com/lucasastorian/sec2md) to convert filing HTML into clean markdown pages, extracted sections, and page-aware chunks. The app still downloads SEC documents through its own SEC client so User-Agent, retry, rate limiting, and failure handling remain centralized.

Parse a stored filing after metadata ingestion:

```powershell
$filings = Invoke-RestMethod "http://127.0.0.1:8000/companies/AAPL/filings?form_type=10-K&limit=1"
$parseJob = Invoke-RestMethod -Method Post "http://127.0.0.1:8000/filings/$($filings[0].id)/parse"
$parseJob
```

Check parse status and read sections/chunks:

```powershell
Invoke-RestMethod "http://127.0.0.1:8000/jobs/$($parseJob.id)"
Invoke-RestMethod "http://127.0.0.1:8000/filings/$($filings[0].id)/sections"
Invoke-RestMethod "http://127.0.0.1:8000/filings/$($filings[0].id)/chunks?limit=10"
```

Force a fresh filing HTML download and re-parse:

```powershell
Invoke-RestMethod -Method Post "http://127.0.0.1:8000/filings/$($filings[0].id)/parse?refresh=true"
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
- `POST /filings/{filing_id}/parse?refresh=false`
- `GET /filings/{filing_id}/sections`
- `GET /filings/{filing_id}/sections/{section_id}`
- `GET /filings/{filing_id}/chunks?section_id=&limit=`
- `GET /filings/{filing_id}/chunks/{chunk_id}/source`

## Verification

Backend tests:

```powershell
Set-Location backend
.\.venv\Scripts\python -m pytest
```

Frontend build:

```powershell
Set-Location frontend
npm run build
```

Health check:

```powershell
Invoke-RestMethod http://127.0.0.1:8000/health
```

## Data Limitations

- The system currently stores SEC filing metadata, raw filing HTML, parsed section markdown, and document chunks.
- SEC data can be delayed, amended, incomplete, or inconsistent across forms and companies.
- Filing date and report date are different concepts and should not be treated as interchangeable.
- M3 parses the primary SEC HTML document only; 8-K exhibit files are not downloaded as separate documents yet.
- `sec2md` only supports HTML input. PDF or non-HTML primary documents are marked as parse failures.
- Chunk highlighted-source pages are generated dynamically from stored annotated HTML and chunk element ids.
- Later milestones will add XBRL metrics, retrieval, citations, and answer validation.

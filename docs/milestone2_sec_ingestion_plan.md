# Milestone 2 Plan: SEC Ingestion

## Summary

Milestone 2 is backend/API-first. It adds SEC company lookup, SEC submissions ingestion, filing metadata storage, response caching, request rate limiting, retry handling, and job progress updates.

No frontend ingestion UI will be added in this milestone.

## Implementation Steps

1. Dependencies and configuration
   - Add `httpx`.
   - Extend SEC settings for `SEC_USER_AGENT`, default rate limit, and cache TTL.
   - Make SEC calls fail visibly when `SEC_USER_AGENT` is missing.

2. Database schema
   - Add `companies`, `filings`, and `sec_response_cache`.
   - Add SQLAlchemy models, Pydantic schemas, and an Alembic migration.
   - Keep this step structural only; do not connect live SEC requests yet.

3. SEC client foundation
   - Implement a unified SEC HTTP client.
   - Attach the configured `User-Agent` to every SEC request.
   - Add rate limiting.
   - Add retry and backoff behavior.
   - Keep this layer responsible only for making reliable SEC requests.

4. SEC response cache
   - Store JSON responses by URL or cache key.
   - Support cache hit, cache miss, expiry, and `refresh=true` cache bypass.
   - Validate cache behavior with unit tests before wiring the full ingestion flow.

5. Ticker to CIK and company upsert
   - Resolve tickers through SEC `company_tickers.json`.
   - Normalize ticker values and zero-pad CIK values.
   - Upsert `companies`.
   - Add `GET /companies/search` and `GET /companies/{ticker}`.

6. Submissions to filing metadata
   - Fetch SEC submissions for the resolved CIK.
   - Keep only `10-K`, `10-Q`, and `8-K`.
   - Build SEC filing detail URLs and primary document URLs.
   - Upsert `filings`.

7. Ingestion orchestration and jobs
   - Add `POST /companies/{ticker}/ingest`.
   - Create a `sec_ingestion` job.
   - Use FastAPI `BackgroundTasks` to run company resolution, submissions fetch, and filing storage.
   - Update job progress, payload, retry count, and error message at each stage.
   - Mark failed jobs explicitly instead of silently ignoring failures.

8. Read APIs and acceptance tests
   - Add `GET /companies/{ticker}/jobs`.
   - Add `GET /companies/{ticker}/filings`.
   - Add mocked SEC integration tests.
   - Manually verify `AAPL`, `TSLA`, and `NVDA` ingestion with a real `SEC_USER_AGENT`.

## Key Changes

- Add runtime SEC HTTP support with `httpx`, backed by a small SEC service layer.
- Resolve tickers through SEC `company_tickers.json`.
- Fetch submissions through `data.sec.gov/submissions/CIK##########.json`.
- Require `SEC_USER_AGENT`; SEC calls should fail visibly if it is missing.
- Add a process-local SEC rate limiter, defaulting to `10 requests/second`.
- Add retry handling for timeouts, `429`, and `5xx` responses.
- Do not retry normal `4xx` responses such as `404`.
- Add database-backed JSON caching for SEC metadata responses.
- Add ingestion job progress updates for company resolution, submissions fetch, filing upsert, cache usage, retries, success, and failure.

## Database Changes

Add models and an Alembic migration for:

- `companies`
  - Normalized ticker
  - Zero-padded CIK
  - Company name
  - Exchange, SIC, and sector/industry fields if available from SEC metadata

- `filings`
  - Company id
  - Accession number
  - Form type
  - Filing date
  - Report date
  - Primary document
  - SEC filing detail URL
  - SEC primary document URL

- `sec_response_cache`
  - Cache key
  - URL
  - JSON response body
  - HTTP status code
  - Fetched timestamp
  - Expiry timestamp

Keep the existing `jobs.company_id` nullable and indexed. Update it after company resolution, but do not add a database foreign key in this milestone.

## Ingestion Flow

- Add `POST /companies/{ticker}/ingest?refresh=false`.
- The endpoint creates a `sec_ingestion` job and runs ingestion with FastAPI `BackgroundTasks`.
- Normalize the ticker and resolve it to a CIK.
- Upsert the company record.
- Fetch SEC submissions.
- Filter recent filings to `10-K`, `10-Q`, and `8-K`.
- Build SEC archive links from accession number and primary document.
- Upsert filing metadata.
- Mark the job as `succeeded` with counts and cache statistics.
- On failure, mark the job as `failed` with a useful error message.

## Public API / Types

Add API endpoints:

- `GET /companies/search?q=...`
- `GET /companies/{ticker}`
- `POST /companies/{ticker}/ingest?refresh=false`
- `GET /companies/{ticker}/jobs?limit=...`
- `GET /companies/{ticker}/filings?form_type=&limit=`

Add Pydantic schemas:

- `CompanyRead`
- `FilingRead`
- `CompanySearchResult`

Reuse the existing `JobRead` schema.

## Test Plan

Unit tests:

- Ticker normalization.
- CIK zero-padding.
- SEC URL construction for filing detail and primary document pages.
- Filing filter keeps only `10-K`, `10-Q`, and `8-K`.
- Cache hit avoids an HTTP request.
- `refresh=true` bypasses cache.
- Rate limiter calls the sleeper before rapid network requests.
- Retry handles timeout, `429`, and `5xx`.
- Retry does not run for normal `4xx`.
- Ingestion job succeeds with mocked SEC fixtures.
- Missing ticker or SEC failure marks the job as `failed` with a useful error.

API tests:

- `POST /companies/AAPL/ingest` creates a job.
- Company, jobs, and filings endpoints return persisted mocked data.
- Unknown company/ticker returns a clear `404`.
- Existing `/jobs` behavior remains compatible.

Manual acceptance:

- With a real `SEC_USER_AGENT`, ingest `AAPL`, `TSLA`, and `NVDA`.
- Verify recent `10-K`, `10-Q`, and `8-K` metadata are stored with SEC links.
- Re-run ingestion and confirm cached SEC responses are used unless `refresh=true`.

## Assumptions

- Milestone 2 does not download filing HTML; that remains Milestone 3.
- Milestone 2 does not ingest XBRL company facts; that remains Milestone 4.
- The cache is DB-backed JSON for SEC metadata only; raw filing document caching comes later.
- FastAPI `BackgroundTasks` is sufficient for this milestone; a durable worker or queue can replace it later without changing the public API.

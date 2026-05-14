# Milestone 3 Incremental Plan: Filing Download, Parsing, and Chunking

## Summary

Milestone 3 turns stored SEC filing metadata into clean, searchable, citeable filing text.

This milestone should be implemented incrementally. Each change should be small enough to explain clearly, test in isolation, and stop before moving to the next step. The goal is not to rush to a demo; the goal is to build a strong filing text pipeline that can support later embeddings, retrieval, citation validation, and Q&A.

Milestone 3 includes:

- Raw filing HTML download and local cache.
- Filing document metadata storage.
- HTML cleanup and normalized text extraction.
- Major section extraction for `10-K`, `10-Q`, and `8-K` filings.
- Section-bounded chunking with stable citation metadata.
- Read APIs for sections and chunks.
- Filing Explorer UI for reviewing filings, sections, chunks, and source links.

Milestone 3 does not include embeddings, XBRL metrics, or AI-generated answers.

## Working Style

Implement exactly one change at a time.

After each change:

- Explain what changed.
- Explain why the change exists.
- Explain how it connects to the next step.
- Run focused tests for that change.
- Stop before beginning the next change.

This cadence is intentional. The filing pipeline is foundational for the rest of the product, and every layer should be easy to inspect and reason about.

## Implementation Steps

1. Documentation-only plan
   - Add this document.
   - Capture the architecture, sequence, APIs, tests, and assumptions.
   - Do not change application code in this step.

2. Database foundation
   - Add SQLAlchemy models and an Alembic migration for:
     - `filing_documents`
     - `filing_sections`
     - `document_chunks`
   - Add basic schema tests.
   - Do not download or parse filings yet.

3. Filing document download
   - Add a backend service that downloads `filings.sec_primary_document_url`.
   - Reuse the existing SEC client behavior for `User-Agent`, rate limiting, retry, and visible failures.
   - Cache raw filing HTML in a local cache directory.
   - Store cache metadata in `filing_documents`.
   - Add mocked download/cache tests.

4. Filing processing job
   - Add a `filing_processing` job type.
   - Add job creation and execution for filing processing.
   - Add `POST /filings/{filing_id}/process?refresh=false`.
   - At this stage, the job should only download and cache the raw filing document.
   - Stop and verify job lifecycle behavior before adding parsing.

5. HTML cleanup and fallback text extraction
   - Add `selectolax` for fast HTML cleanup.
   - Add `inscriptis` for layout-aware HTML-to-text conversion.
   - Store a fallback `full_document` section.
   - Add fixture-based parser tests.
   - Do not add SEC item detection yet.

6. Section extraction
   - Add SEC-aware section extraction using `sec-parser` where possible.
   - Add deterministic regex fallback for common SEC item headings.
   - Target major `10-K`, `10-Q`, and `8-K` sections.
   - Store section confidence and extraction method.
   - Keep `full_document` fallback behavior for weak or unusual filings.

7. Chunking
   - Add section-bounded chunk creation.
   - Use `tiktoken` for token counts.
   - Prefer semantic paragraph, list, and table boundaries before token-size splitting.
   - Store chunk offsets, hashes, token counts, and citation metadata.
   - Add tests proving chunks do not cross section boundaries.

8. Read APIs
   - Add `GET /filings/{filing_id}/sections`.
   - Add `GET /filings/{filing_id}/chunks?section_id=&limit=`.
   - Extend filing responses with lightweight processing status and parsed counts if practical.
   - Keep existing Milestone 2 endpoints backward compatible.

9. Filing Explorer UI
   - Add a company/ticker entry point that displays stored filings.
   - Show form type, filing date, report date, processing status, and SEC source links.
   - Allow processing or reprocessing a filing.
   - Show extracted sections in filing order.
   - Show selected section text and chunks.
   - Show citation metadata for each chunk.
   - Keep the UI dense, professional, and analyst-oriented.

## Key Technical Choices

- Use the existing SEC client as the only network layer for SEC requests.
- Store raw filing HTML in local filesystem cache for development.
- Store raw document metadata, sections, and chunks in PostgreSQL.
- Use deterministic parsing and chunking rather than LLM-based parsing.
- Use `selectolax` for HTML cleanup, `inscriptis` for normalized text extraction, `sec-parser` for SEC-aware structure, and `tiktoken` for token counts.
- Preserve enough metadata for every chunk to become a future citation target.

## Database Changes

Add models and a migration for:

- `filing_documents`
  - Filing id
  - Source URL
  - Local cache path
  - Content SHA-256
  - Content type
  - Byte size
  - Download status
  - Parser version
  - Error message
  - Created and updated timestamps

- `filing_sections`
  - Filing id
  - Section key
  - Section title
  - Section order
  - Normalized text
  - Start and end offsets
  - Extraction confidence
  - Extraction method
  - Created and updated timestamps

- `document_chunks`
  - Filing id
  - Section id
  - Chunk index
  - Chunk text
  - Token count
  - Start and end offsets
  - Text hash
  - Accession number
  - Form type
  - Filing date
  - Section key
  - SEC URL
  - Created and updated timestamps

Reprocessing should be idempotent. It should update or replace deterministic outputs instead of creating duplicate sections and chunks.

## Public API / Types

Add API endpoints:

- `POST /filings/{filing_id}/process?refresh=false`
- `GET /filings/{filing_id}/sections`
- `GET /filings/{filing_id}/chunks?section_id=&limit=`

Add Pydantic schemas:

- `FilingDocumentRead`
- `FilingSectionRead`
- `DocumentChunkRead`

Reuse the existing `JobRead` schema for filing processing jobs.

## Test Plan

Schema tests:

- New tables are created by Alembic.
- Foreign keys and uniqueness constraints behave as expected.
- Required citation metadata columns are present.

Download/cache tests:

- Primary document download uses configured SEC headers and rate limiting.
- Cache hit avoids duplicate download unless `refresh=true`.
- Missing primary document URL creates a visible failure.
- Raw document metadata stores hash, path, size, status, and URL.

Job tests:

- `POST /filings/{filing_id}/process` creates a `filing_processing` job.
- Successful jobs update progress and status.
- Failed jobs store a useful error message.

Parser tests:

- HTML cleanup removes scripts/styles but preserves headings, paragraphs, lists, and tables.
- Fallback `full_document` section is created from valid HTML.
- Major `10-K`, `10-Q`, and `8-K` sections are extracted from fixtures.
- Weak section extraction keeps text through the fallback path.

Chunking tests:

- Chunks do not cross section boundaries.
- Short sections remain as one chunk.
- Long sections split into ordered chunks.
- Each chunk includes accession number, form type, filing date, section, SEC URL, offsets, and token count.

API tests:

- Sections endpoint returns sections in filing order.
- Chunks endpoint supports section filtering and limit.
- Existing company, filing, and job APIs remain compatible.

Manual acceptance:

- Ingest and process latest `10-K` and `10-Q` for `AAPL`, `TSLA`, and `NVDA`.
- Verify major sections such as risk factors and MD&A appear when present.
- Verify Filing Explorer displays filings, sections, chunks, and SEC source links.
- Verify processing failures are visible rather than silent.

## Assumptions

- Milestone 3 starts with this documentation-only step.
- FastAPI `BackgroundTasks` remains acceptable for this milestone.
- Raw filing HTML cache starts as local filesystem storage.
- Parser quality and citation metadata are more important than rushing to embeddings.
- Embeddings remain Milestone 5.
- XBRL metrics remain Milestone 4.
- Citation-grounded Q&A remains Milestone 6.

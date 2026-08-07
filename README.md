# Equity Research Copilot

Ask a question about a US public company and get an answer where every claim traces back to a specific passage in a specific SEC filing.

[中文说明](./README.zh-CN.md)

The whole project exists because of one failure mode: a general-purpose chatbot will confidently recite Apple's FY2024 revenue from memory and be off by a fiscal year, or cite a 10-K page that doesn't actually say what it claims. So this app never lets the model be the source. It pulls filings from EDGAR, parses them into page-anchored chunks, and answers only from what it retrieved — dollar figures come from SEC XBRL facts rather than from generation, citation markers are checked against the evidence that was actually fetched, and questions the filings can't support get declined instead of guessed.

## What you can do with it

**Filings** — look up a ticker, ingest its recent 10-K / 10-Q / 8-K metadata from EDGAR, parse a filing into sections and page-aware chunks, and click any chunk through to its highlighted location in the original SEC HTML.

**Metrics** — revenue, gross profit, operating income, net income, cash flow, capex, and the margins derived from them, normalized out of SEC XBRL company facts. Tags a company doesn't report show as unavailable instead of being inferred.

**Research** — the Q&A view. Ask something like *"what drove Apple's revenue growth last quarter?"* and you get a cited answer alongside the full trace: the query plan, every tool the agent called, the evidence it selected, and whether validation passed.

## How an answer gets built

1. **Plan** — an LLM turns the question into a structured plan: question type, which metrics are involved, which filing sections to target, which forms, what time scope.
2. **Retrieve** — a ReAct agent loops over six tools (XBRL metrics, filing chunks, MD&A, risk factors, segment discussion, prior filings), picking the next one based on which evidence it's still missing. Dense retrieval (pgvector HNSW) and lexical retrieval are fused with RRF, then reranked on filing metadata.
3. **Answer** — generation runs against the selected evidence pack only, emitting citation markers as it goes.
4. **Validate** — citation IDs must resolve to evidence that was actually retrieved (invalid ones are stripped before the answer ships), an answerability gate declines questions the evidence can't support, and a claim-level entailment check flags sentences the cited text doesn't back.

Measured over 6 companies and 139 parsed filings: **86.5% accuracy on structured financial figures** graded against SEC XBRL ground truth (n=96, 95% CI 78–92%), and P50 end-to-end latency of 13.4s. The questions are generated automatically across every company, metric, and fiscal year rather than hand-picked, so there's no selection bias — and the number isn't 100% because operating cash flow (YTD vs. quarterly framing) and older fiscal years are genuinely harder. The harnesses that produce these numbers live in `backend/app/evals/`.

**Stack:** FastAPI · SQLAlchemy · Alembic · PostgreSQL + pgvector · React + Vite + TypeScript · OpenAI embeddings and completions · [`sec2md`](https://github.com/lucasastorian/sec2md) for filing parsing.

## Running it locally

You need Python 3.11+, Node 20.19+ (or 22.12+), Docker, and an OpenAI API key.

**1. Configure the backend.**

```bash
cp backend/.env.example backend/.env
```

Two values must be filled in — everything else has a working default:

- `SEC_USER_AGENT` — the SEC requires a real app name and contact email, e.g. `Equity Research Copilot/0.1 (contact: you@example.com)`
- `OPENAI_API_KEY` — used for embeddings, query planning, and answer generation

**2. Start Postgres.**

```bash
docker compose -f compose.yaml up -d postgres
```

**3. Start the API.**

```bash
cd backend
python3 -m venv .venv
./.venv/bin/python -m pip install -e ".[dev]"
./.venv/bin/alembic upgrade head
./.venv/bin/uvicorn app.main:app --reload --port 8000
```

**4. Start the frontend** in a second terminal. It proxies API calls to port 8000.

```bash
cd frontend
npm install
npm run dev
```

Open the URL Vite prints, and you're in.

### Loading a company

The database starts empty, so pick a ticker and walk it through the pipeline. The Filings view does all of this with buttons; the equivalent from a terminal:

```bash
# Ingest filing metadata from EDGAR
curl -X POST "http://127.0.0.1:8000/companies/AAPL/ingest"

# Parse the latest 10-K into sections and chunks
FILING=$(curl -s "http://127.0.0.1:8000/companies/AAPL/filings?form_type=10-K&limit=1" \
  | python3 -c 'import json,sys; print(json.load(sys.stdin)[0]["id"])')
curl -X POST "http://127.0.0.1:8000/filings/$FILING/parse"

# Embed the chunks and load XBRL financials
curl -X POST "http://127.0.0.1:8000/companies/AAPL/embeddings/generate"
curl -X POST "http://127.0.0.1:8000/companies/AAPL/metrics/load"
```

Each of these returns a job and runs in the background — poll `GET /jobs/{id}` or just watch the UI. Embedding a full 10-K takes a minute or two. Once it's done, ask a question:

```bash
curl -X POST "http://127.0.0.1:8000/research/runs" \
  -H "Content-Type: application/json" \
  -d '{"ticker":"AAPL","question":"What drove revenue growth last quarter?"}'
```

Interactive API docs are at `http://127.0.0.1:8000/docs`.

*On Windows, use `py -3 -m venv .venv` and `.venv\Scripts\` instead of `./.venv/bin/`.*

## Tests

```bash
cd backend && ./.venv/bin/python -m pytest
```

317 tests, all offline — the SEC and LLM clients are Protocols with fakes injected, so no API key or network access is needed to run them.

The evaluation harnesses in `backend/app/evals/` are the opposite: they run against a seeded database and the real API. They measure metric accuracy against XBRL, retrieval recall, answer faithfulness via an LLM judge, and agent tool-selection.

```bash
cd backend && .venv/bin/python -m app.evals.run_all
```

## Layout

```
backend/app/services/   SEC client, parsing, embeddings, retrieval, agent, answer generation
backend/app/api/routes/ companies, filings, research, jobs, health
backend/app/evals/      accuracy, recall, faithfulness, and trajectory harnesses
backend/tests/          offline test suite
frontend/src/           React UI — filings, metrics, research
docs/                   evaluation results, dev log, design notes
```

---

Research tooling, not investment advice. SEC data can be delayed, amended, or inconsistent across forms, and a citation-checked answer is still a starting point for your own reading — not a substitute for it.

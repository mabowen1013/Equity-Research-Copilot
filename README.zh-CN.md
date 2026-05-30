# Equity Research Copilot

[English Version](./README.md)

Equity Research Copilot 是一个面向美国上市公司的全栈投资研究助手。当前后端已经支持 SEC 公司与财报元数据摄取、SEC 财报 HTML 下载、`sec2md` 解析、章节提取、针对近期 `10-K`、`10-Q` 和 `8-K` 财报的 citation-ready 文档切片存储、基于 SEC company facts 的标准化 XBRL 财务指标、chunk embeddings、semantic retrieval、metric-aware retrieval，以及面向后续答案生成的 evidence packaging。

本项目仅用于研究辅助，不构成任何投资建议。

## 当前范围

已实现：

- FastAPI 后端与 React 前端基础框架。
- 基于 Alembic migrations 的 PostgreSQL 数据库配置，并支持 pgvector。
- 健康检查、请求日志和环境配置。
- Job 状态追踪 API。
- SEC ticker 到 CIK / 公司信息的查询。
- 对近期 `10-K`、`10-Q` 和 `8-K` 财报元数据的 SEC submissions 摄取。
- SEC 响应缓存，并支持可选的 refresh bypass。
- SEC 请求 User-Agent、速率限制、重试和失败处理。
- 通过项目统一 SEC client 下载财报 HTML。
- 原始财报文档与 annotated filing document 缓存。
- 使用 `sec2md` 解析财报章节与 page-aware chunks。
- Filing Explorer UI，用于元数据摄取、财报解析、章节、切片和源链接查看。
- 为 v1 财务指标集合加载 XBRL company facts。
- 带来源可追溯性的 free cash flow 和 margin 计算指标。
- Metrics UI，支持缺失 facts 的 unavailable 状态展示。
- Embedding provider interface，以及带版本号 embedding inputs 的批量 chunk embedding 生成。
- Dense retrieval、lexical retrieval、XBRL fact retrieval、rule-based query planning、可选 LLM planner fallback、RRF fusion、metadata reranking 和 retrieval trace 输出。
- Final evidence pack selection，包含按角色分组的 chunks、selected evidence spans、metric comparisons 和稳定 evidence ids。
- Developer/debug retrieval API 和前端 Evidence Retrieval 视图。
- Answer evidence context contract（`answer_evidence_context.v1`），用于后续 answer service 和 validator 集成。
- Retrieval dump 和 gold-eval 工具。
- 公司、财报、解析、指标、embedding、retrieval 和 job 读取 API。

暂未实现：

- Citation-grounded answer generation 和 citation validation。
- 将检索证据转化为最终答案的前端 Q&A workflow。
- 生产级检索优化，例如 HNSW auto mode、MMR diversity、neighbor expansion、learned reranking，以及更完整的 eval coverage。

## 前置要求

- Python 3.11+
- Node.js 20.19+ 或 22.12+
- 安装 Docker Compose 的 Docker Desktop

## 必需环境变量

后端环境变量从 `backend/.env` 加载。可以从示例文件开始创建：

macOS/Linux：

```bash
cp backend/.env.example backend/.env
```

Windows PowerShell：

```powershell
Copy-Item backend/.env.example backend/.env
```

必需值：

| 变量 | 是否必需 | 描述 |
| --- | --- | --- |
| `DATABASE_URL` | 否 | PostgreSQL 连接 URL。默认使用本地 Docker Compose 数据库。 |
| `SEC_USER_AGENT` | 是 | 发送给 SEC API 的 User-Agent。应包含应用名称和联系邮箱。 |
| `SEC_RATE_LIMIT_PER_SECOND` | 否 | SEC 请求限制。默认值为 `10`，也是应用配置允许的最大值。 |
| `SEC_CACHE_TTL_SECONDS` | 否 | SEC JSON 响应缓存 TTL。默认值为 `86400` 秒。 |
| `OPENAI_API_KEY` | embeddings 和 LLM planning 需要 | 默认 embedding provider 和 LLM-first query planning 使用的 OpenAI API key。即使没有 dense embeddings，retrieval 仍可降级为 lexical 和 XBRL facts 检索；如果 LLM 不可用，query planning 会降级成宽泛文本检索。 |
| `EMBEDDING_PROVIDER` | 否 | Embedding provider。默认值为 `openai`。 |
| `EMBEDDING_MODEL` | 否 | Embedding 模型。默认值为 `text-embedding-3-small`。 |
| `EMBEDDING_DIMENSIONS` | 否 | Embedding 向量维度。默认值为 `1536`。 |
| `EMBEDDING_INPUT_VERSION` | 否 | 文档 embedding input template 的版本号。默认值为 `v1`。 |
| `VECTOR_SEARCH_MODE` | 否 | 预留的向量检索 profile。默认值为 `exact`；HNSW 是后续优化。 |
| `RETRIEVAL_DENSE_CANDIDATES` | 否 | Dense retrieval 候选数量预算。默认值为 `40`。 |
| `RETRIEVAL_LEXICAL_CANDIDATES` | 否 | Lexical retrieval 候选数量预算。默认值为 `40`。 |
| `RETRIEVAL_FACT_CANDIDATES` | 否 | XBRL fact 候选数量预算。默认值为 `20`。 |
| `RETRIEVAL_TOP_K` | 否 | Final evidence-pack selection 之前的最终 chunk evidence 数量。默认值为 `10`。 |
| `QUERY_PLANNER_MODE` | 否 | Query planner 模式。默认值为 `llm`。兼容旧值 `rule_only` 和 `rule_with_llm_fallback`；其中 `rule_with_llm_fallback` 现在也走 LLM-first planner。 |
| `QUERY_PLANNER_LLM_MODEL` | 否 | LLM planner 使用的模型。默认值为 `gpt-4o-mini`。 |
| `QUERY_PLANNER_LLM_TIMEOUT_SECONDS` | 否 | LLM planner 调用超时时间。默认值为 `20`。 |
| `QUERY_PLANNER_LLM_MAX_RETRIES` | 否 | Planner 调用的 OpenAI SDK 重试次数。默认值为 `0`，本地测试时会更快失败，不会被 SDK 自动重试拖住。 |

示例：

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
VECTOR_SEARCH_MODE="exact"
RETRIEVAL_DENSE_CANDIDATES=40
RETRIEVAL_LEXICAL_CANDIDATES=40
RETRIEVAL_FACT_CANDIDATES=20
RETRIEVAL_TOP_K=10
QUERY_PLANNER_MODE="llm"
QUERY_PLANNER_LLM_MODEL="gpt-4o-mini"
QUERY_PLANNER_LLM_TIMEOUT_SECONDS=20
QUERY_PLANNER_LLM_MAX_RETRIES=0
```

## 本地开发

从仓库根目录启动 PostgreSQL：

```bash
docker compose -f compose.yaml up -d postgres
```

安装后端依赖并启动 API。

macOS/Linux：

```bash
cd backend
python3 -m venv .venv
./.venv/bin/python -m pip install -e ".[dev]"
./.venv/bin/alembic upgrade head
./.venv/bin/uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

Windows PowerShell：

```powershell
Set-Location backend
py -3 -m venv .venv
.\.venv\Scripts\python -m pip install -e .[dev]
.\.venv\Scripts\alembic upgrade head
.\.venv\Scripts\uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

如果 Windows Python launcher 不可用，可以将 `py -3` 替换为 `python`。

在另一个终端中安装并启动前端。

macOS/Linux：

```bash
cd frontend
npm install
npm run dev
```

Windows PowerShell：

```powershell
Set-Location frontend
npm install
npm run dev
```

前端开发服务器会将 `/health`、`/companies`、`/filings`、`/jobs` 和 `/research` 代理到 `http://127.0.0.1:8000` 的后端服务。

## SEC 数据摄取

先启动后端，然后在另一个终端中触发数据摄取。

获取 Apple 的最新 SEC 元数据。财报元数据摄取默认绕过 SEC 响应缓存，因此可以及时获取新接受的 `10-K`、`10-Q` 和 `8-K` 财报。

macOS/Linux：

```bash
JOB_ID=$(curl -s -X POST "http://127.0.0.1:8000/companies/AAPL/ingest" | python3 -c 'import json, sys; print(json.load(sys.stdin)["id"])')
curl "http://127.0.0.1:8000/jobs/$JOB_ID"
curl "http://127.0.0.1:8000/companies/AAPL"
curl "http://127.0.0.1:8000/companies/AAPL/filings"
curl "http://127.0.0.1:8000/companies/AAPL/filings?form_type=10-K"
```

Windows PowerShell：

```powershell
$job = Invoke-RestMethod -Method Post "http://127.0.0.1:8000/companies/AAPL/ingest"
Invoke-RestMethod "http://127.0.0.1:8000/jobs/$($job.id)"
Invoke-RestMethod "http://127.0.0.1:8000/companies/AAPL"
Invoke-RestMethod "http://127.0.0.1:8000/companies/AAPL/filings"
Invoke-RestMethod "http://127.0.0.1:8000/companies/AAPL/filings?form_type=10-K"
```

运行 demo tickers：

macOS/Linux：

```bash
curl -X POST "http://127.0.0.1:8000/companies/AAPL/ingest?refresh=true"
curl -X POST "http://127.0.0.1:8000/companies/TSLA/ingest?refresh=true"
curl -X POST "http://127.0.0.1:8000/companies/NVDA/ingest?refresh=true"
```

Windows PowerShell：

```powershell
Invoke-RestMethod -Method Post "http://127.0.0.1:8000/companies/AAPL/ingest?refresh=true"
Invoke-RestMethod -Method Post "http://127.0.0.1:8000/companies/TSLA/ingest?refresh=true"
Invoke-RestMethod -Method Post "http://127.0.0.1:8000/companies/NVDA/ingest?refresh=true"
```

只有当你明确希望复用未过期的 SEC 响应缓存时，才传入 `refresh=false`。

如需直接检查已缓存的 SEC 响应：

```bash
docker exec -it equity_research_copilot_postgres psql -U equity_research -d equity_research_copilot
```

```sql
SELECT id, url, status_code, fetched_at, expires_at
FROM sec_response_cache
ORDER BY fetched_at DESC;
```

## 财报解析

Milestone 3 使用 [`sec2md`](https://github.com/lucasastorian/sec2md) 将财报 HTML 转换为干净的 markdown pages、提取出的 sections 和 page-aware chunks。应用仍然通过自己的 SEC client 下载 SEC 文档，因此 User-Agent、重试、速率限制和失败处理都保持在统一中心化逻辑中。

在完成元数据摄取后，解析已存储的财报。

macOS/Linux：

```bash
FILING_ID=$(curl -s "http://127.0.0.1:8000/companies/AAPL/filings?form_type=10-K&limit=1" | python3 -c 'import json, sys; print(json.load(sys.stdin)[0]["id"])')
PARSE_JOB_ID=$(curl -s -X POST "http://127.0.0.1:8000/filings/$FILING_ID/parse" | python3 -c 'import json, sys; print(json.load(sys.stdin)["id"])')
curl "http://127.0.0.1:8000/jobs/$PARSE_JOB_ID"
curl "http://127.0.0.1:8000/filings/$FILING_ID/sections"
curl "http://127.0.0.1:8000/filings/$FILING_ID/chunks?limit=10"
```

Windows PowerShell：

```powershell
$filings = Invoke-RestMethod "http://127.0.0.1:8000/companies/AAPL/filings?form_type=10-K&limit=1"
$parseJob = Invoke-RestMethod -Method Post "http://127.0.0.1:8000/filings/$($filings[0].id)/parse"
Invoke-RestMethod "http://127.0.0.1:8000/jobs/$($parseJob.id)"
Invoke-RestMethod "http://127.0.0.1:8000/filings/$($filings[0].id)/sections"
Invoke-RestMethod "http://127.0.0.1:8000/filings/$($filings[0].id)/chunks?limit=10"
```

强制重新下载最新的财报 HTML 并重新解析：

macOS/Linux：

```bash
curl -X POST "http://127.0.0.1:8000/filings/$FILING_ID/parse?refresh=true"
```

Windows PowerShell：

```powershell
Invoke-RestMethod -Method Post "http://127.0.0.1:8000/filings/$($filings[0].id)/parse?refresh=true"
```

## 检索与证据

Milestone 5 retrieval 已经实现。系统可以为已解析的 filing chunks 生成 embeddings，根据用户问题检索相关财报证据，包含 metric-aware XBRL facts 和 comparisons，并为后续 answer generation 与 validation 返回稳定 evidence ids。

在财报解析完成后生成 embeddings：

macOS/Linux：

```bash
EMBED_JOB_ID=$(curl -s -X POST "http://127.0.0.1:8000/companies/AAPL/embeddings/generate" | python3 -c 'import json, sys; print(json.load(sys.stdin)["id"])')
curl "http://127.0.0.1:8000/jobs/$EMBED_JOB_ID"
```

Windows PowerShell：

```powershell
$embedJob = Invoke-RestMethod -Method Post "http://127.0.0.1:8000/companies/AAPL/embeddings/generate"
Invoke-RestMethod "http://127.0.0.1:8000/jobs/$($embedJob.id)"
```

针对指标相关问题加载 XBRL metrics：

macOS/Linux：

```bash
METRICS_JOB_ID=$(curl -s -X POST "http://127.0.0.1:8000/companies/AAPL/metrics/load?refresh=false" | python3 -c 'import json, sys; print(json.load(sys.stdin)["id"])')
curl "http://127.0.0.1:8000/jobs/$METRICS_JOB_ID"
```

Windows PowerShell：

```powershell
$metricsJob = Invoke-RestMethod -Method Post "http://127.0.0.1:8000/companies/AAPL/metrics/load?refresh=false"
Invoke-RestMethod "http://127.0.0.1:8000/jobs/$($metricsJob.id)"
```

调用 retrieval API：

macOS/Linux：

```bash
curl -s -X POST "http://127.0.0.1:8000/research/retrieve?view=analysis" \
  -H "Content-Type: application/json" \
  -d '{"ticker":"AAPL","question":"What drove Apple revenue growth?"}'
```

Windows PowerShell：

```powershell
Invoke-RestMethod -Method Post "http://127.0.0.1:8000/research/retrieve?view=analysis" `
  -ContentType "application/json" `
  -Body '{"ticker":"AAPL","question":"What drove Apple revenue growth?"}'
```

完整响应包含 `retrieval_plan`、`retrieved_chunks`、`retrieved_facts`、`metric_comparisons`、`source_coverage_summary`、`final_evidence_pack` 和 `retrieval_trace`。紧凑的 `view=analysis` 响应保留同样的诊断结构，但会裁剪长 payload，适合在终端检查。

`final_evidence_pack` 会将已选证据分为 primary financial statement chunks、MD&A explanation chunks、segment or product breakdown chunks、annual context chunks、metric comparisons，以及 selected evidence spans。Spans 是从 retrieved chunks 中挑出的短摘录，因为它们是最直接支撑回答的文本；它们保留 source chunk evidence id、页码元数据、SEC URL 和 selection reasons。

当 embeddings 缺失或不可用时，dense retrieval 会优雅降级；只要条件允许，lexical retrieval 和 XBRL fact retrieval 仍会继续运行。前端已经包含 Evidence Retrieval 视图，可以检查同一份 evidence pack、spans、facts、comparisons 和 retrieval trace。

## 评估工具

从仓库根目录运行 query planner eval：

macOS/Linux：

```bash
PYTHONPATH=backend backend/.venv/bin/python -m app.evals.query_planner_eval
```

Windows PowerShell：

```powershell
$env:PYTHONPATH = "backend"
backend\.venv\Scripts\python -m app.evals.query_planner_eval
```

导出某个问题的 retrieval diagnostics：

macOS/Linux：

```bash
PYTHONPATH=backend backend/.venv/bin/python -m app.evals.retrieval_dump AAPL "What drove Apple revenue growth?"
```

Windows PowerShell：

```powershell
$env:PYTHONPATH = "backend"
backend\.venv\Scripts\python -m app.evals.retrieval_dump AAPL "What drove Apple revenue growth?"
```

运行 retrieval gold eval：

macOS/Linux：

```bash
PYTHONPATH=backend backend/.venv/bin/python -m app.evals.retrieval_gold_eval
```

Windows PowerShell：

```powershell
$env:PYTHONPATH = "backend"
backend\.venv\Scripts\python -m app.evals.retrieval_gold_eval
```

当前 gold eval seed set 位于 `backend/evals/retrieval_gold_eval.json`。它刻意保持小规模；当 chunking、SEC 数据或本地 fixture filings 变化时，应刷新该文件。

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

## 验证

后端测试。

macOS/Linux：

```bash
cd backend
./.venv/bin/python -m pytest
```

Windows PowerShell：

```powershell
Set-Location backend
.\.venv\Scripts\python -m pytest
```

前端构建。

macOS/Linux：

```bash
cd frontend
npm run build
```

Windows PowerShell：

```powershell
Set-Location frontend
npm run build
```

健康检查。

macOS/Linux：

```bash
curl http://127.0.0.1:8000/health
```

Windows PowerShell：

```powershell
Invoke-RestMethod http://127.0.0.1:8000/health
```

## 数据限制

- 系统当前存储 SEC 财报元数据、原始财报 HTML、已解析的 section markdown、document chunks、chunk embeddings、XBRL facts、computed metrics 和 retrieval evidence diagnostics。
- SEC 数据可能存在延迟、修订、不完整，或在不同 forms 和 companies 之间存在不一致。
- Filing date 和 report date 是不同概念，不应混为一谈。
- M3 目前只解析 SEC primary HTML document；`8-K` exhibit files 暂未作为独立文档下载。
- `sec2md` 只支持 HTML 输入。PDF 或非 HTML primary documents 会被标记为解析失败。
- Chunk highlighted-source pages 会从已存储的 annotated HTML 和 chunk element ids 动态生成。
- XBRL metrics 使用保守的 US-GAAP tag mapping。缺失指标会显示为 unavailable，而不是被系统猜测。
- Milestone 5 当前返回 evidence、facts、spans、comparisons 和 trace data；还不会生成最终自然语言答案。
- Query planning 现在默认直接由 LLM 解析。LLM 不可用时，后端会降级为宽泛文本检索，而不是继续用脆弱的关键词 slot 规则。
- HNSW auto mode、learned reranking、更大规模 eval coverage、answer generation 和 citation validation 会放到后续 milestone。

# Equity Research Copilot

针对美股上市公司提问，得到的每一句结论都能追溯回某份 SEC 财报里的具体段落。

[English Version](./README.md)

做这个项目的起因是一种很具体的失败：通用聊天机器人会非常自信地凭记忆报出 Apple FY2024 的营收，然后差了一个财年；或者煞有介事地引用某页 10-K，而那页根本没写这回事。所以这个应用从设计上就不让模型充当信息来源——它从 EDGAR 抓取财报，解析成带页码锚点的 chunk，只依据检索到的内容作答：金额来自 SEC XBRL facts 而不是模型生成，引用标记会与实际检索到的证据逐一核对，财报支撑不了的问题直接拒答，而不是猜一个。

## 能用它做什么

**Filings（财报）** — 输入 ticker，从 EDGAR 摄取近期 10-K / 10-Q / 8-K 元数据，把某份财报解析成章节和 page-aware chunk，并且能点击任意 chunk 跳回原始 SEC HTML 中高亮的位置。

**Metrics（指标）** — 营收、毛利、营业利润、净利润、现金流、资本开支，以及由它们推导的各项 margin，全部从 SEC XBRL company facts 标准化而来。公司没有披露对应 tag 的指标显示为 unavailable，不做推断。

**Research（研究）** — 问答视图。问一句 *"什么驱动了 Apple 上个季度的营收增长？"*，除了带引用的答案，还能看到完整 trace：查询计划、agent 调用的每一个工具、它选中的证据，以及验证是否通过。

## 一个答案是怎么生成的

1. **Plan（规划）** — LLM 把问题解析成结构化计划：问题类型、涉及哪些指标、该查哪些财报章节、哪类表单、时间范围。
2. **Retrieve（检索）** — ReAct agent 在六个工具间循环（XBRL 指标、财报 chunk、MD&A、风险因素、分部讨论、历史财报），根据"还缺哪类证据"决定下一步调哪个。dense 检索（pgvector HNSW）与 lexical 检索用 RRF 融合，再按财报元数据重排。
3. **Answer（生成）** — 只基于选定的 evidence pack 生成答案，边写边标引用。
4. **Validate（验证）** — 引用 ID 必须能对应到真实检索过的证据（非法引用在答案输出前剥除）；answerability 闸门拒绝证据支撑不了的问题；claim 级 entailment 检查标出引用文本并不支持的句子。

在 6 家公司、139 份已解析财报上的实测：**结构化财务数字准确率 86.5%**，以 SEC XBRL 为真值判定（n=96，95% CI 78–92%），端到端延迟 P50 13.4s。题目是按「每家公司 × 每个指标 × 每个财年」自动生成的，不是手挑，所以没有选择偏差；之所以不是 100%，是因为经营现金流（累计口径 vs 单季口径）和更早的财年确实更难。产出这些数字的 harness 在 `backend/app/evals/`。

**技术栈：** FastAPI · SQLAlchemy · Alembic · PostgreSQL + pgvector · React + Vite + TypeScript · OpenAI embeddings 与 completions · 财报解析用 [`sec2md`](https://github.com/lucasastorian/sec2md)。

## 本地启动

需要 Python 3.11+、Node 20.19+（或 22.12+）、Docker，以及一个 OpenAI API key。

**1. 配置后端。**

```bash
cp backend/.env.example backend/.env
```

只有两个值必须自己填，其余都有可用默认值：

- `SEC_USER_AGENT` — SEC 要求提供真实的应用名和联系邮箱，例如 `Equity Research Copilot/0.1 (contact: you@example.com)`
- `OPENAI_API_KEY` — 用于 embedding、查询规划和答案生成

**2. 启动 Postgres。**

```bash
docker compose -f compose.yaml up -d postgres
```

**3. 启动后端 API。**

```bash
cd backend
python3 -m venv .venv
./.venv/bin/python -m pip install -e ".[dev]"
./.venv/bin/alembic upgrade head
./.venv/bin/uvicorn app.main:app --reload --port 8000
```

**4. 另开一个终端启动前端**，它会把 API 请求代理到 8000 端口。

```bash
cd frontend
npm install
npm run dev
```

打开 Vite 输出的地址即可。

### 灌入一家公司的数据

数据库初始是空的，所以要挑一个 ticker 走一遍流程。Filings 视图里点按钮就能完成，命令行等价写法：

```bash
# 从 EDGAR 摄取财报元数据
curl -X POST "http://127.0.0.1:8000/companies/AAPL/ingest"

# 解析最新一份 10-K 为章节和 chunk
FILING=$(curl -s "http://127.0.0.1:8000/companies/AAPL/filings?form_type=10-K&limit=1" \
  | python3 -c 'import json,sys; print(json.load(sys.stdin)[0]["id"])')
curl -X POST "http://127.0.0.1:8000/filings/$FILING/parse"

# 生成 chunk embedding，并加载 XBRL 财务数据
curl -X POST "http://127.0.0.1:8000/companies/AAPL/embeddings/generate"
curl -X POST "http://127.0.0.1:8000/companies/AAPL/metrics/load"
```

这些接口都返回一个 job 并在后台执行——用 `GET /jobs/{id}` 轮询，或者直接看 UI。一份完整 10-K 做 embedding 大概要一两分钟。跑完就可以提问了：

```bash
curl -X POST "http://127.0.0.1:8000/research/runs" \
  -H "Content-Type: application/json" \
  -d '{"ticker":"AAPL","question":"What drove revenue growth last quarter?"}'
```

交互式 API 文档在 `http://127.0.0.1:8000/docs`。

*Windows 上把 `python3 -m venv` 换成 `py -3 -m venv`，路径前缀 `./.venv/bin/` 换成 `.venv\Scripts\`。*

## 测试

```bash
cd backend && ./.venv/bin/python -m pytest
```

317 个测试，全部离线运行——SEC client 和 LLM client 都是 Protocol，测试里注入 fake 实现，所以不需要 API key 也不需要联网。

`backend/app/evals/` 下的评估 harness 则相反：跑在灌好数据的数据库和真实 API 上，测的是指标准确率（对 XBRL）、检索召回、答案忠实度（LLM judge）和 agent 工具选择。

```bash
cd backend && .venv/bin/python -m app.evals.run_all
```

## 目录结构

```
backend/app/services/   SEC client、解析、embedding、检索、agent、答案生成
backend/app/api/routes/ companies、filings、research、jobs、health
backend/app/evals/      准确率、召回、忠实度、agent 轨迹评估
backend/tests/          离线测试套件
frontend/src/           React 前端 — filings、metrics、research
docs/                   评估结果、开发日志、设计记录
```

---

这是研究辅助工具，不构成投资建议。SEC 数据可能延迟、被修订，或在不同表单间不一致；即便是经过引用校验的答案，也只是你自己去读原文的起点，不是替代品。

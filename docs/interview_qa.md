# Equity Research Copilot — 模拟面试 Q&A（AI Agent 岗位）

> 本文档以面试官视角对项目提出问题，并给出基于真实代码的详细回答。
> 所有提到的模块都对应仓库中的实际实现，例如 `backend/app/services/retrieval.py`、
> `research_agent.py`、`answer_generation.py`、`query_planner.py`。

---

## 一、项目整体与动机

### Q1. 用一分钟介绍这个项目：它解决什么问题？为什么不直接用 ChatGPT 问财报问题？

**答：** Equity Research Copilot 是一个针对美股 SEC 财报（10-K / 10-Q / 8-K）的研究助手。用户用自然语言提问（如 "Why did Apple's gross margin change year over year?"），系统返回一段**每个论断都带可点击 SEC 原文引用**的分析师风格回答。

直接用 ChatGPT 有三个无法接受的问题：

1. **幻觉**：通用 LLM 会编造财务数字，而财务数字错一位就是灾难性错误。本项目强制回答只能来自检索到的证据对象，且引用 ID 必须通过 `CitationValidator` 校验，校验失败宁可返回 "insufficient evidence" 的安全兜底，也不输出无据回答。
2. **可追溯性**：投研场景要求每个结论能回溯到 filing 原文的具体章节和页码。系统的每条证据都有稳定的 `evidence_id`（`chunk:1252`、`span:1279:mda_explanation_chunks:966:1052`、`metric_comparison:revenue:...`），引用直接链接到 SEC 官网 URL 加页码。
3. **结构化数据精度**：财务指标不应从文本里"读"，而应从 XBRL 结构化数据里"查"。系统从 SEC company facts 加载规范化的 XBRL 指标（含计算型指标如 FCF、margins），数字来自结构化事实而非 LLM 对文本的理解。

### Q2. 整体架构是怎样的？一次 `/research/runs` 请求经过哪些阶段？

**答：** FastAPI 后端 + React 前端 + PostgreSQL（pgvector）。一次研究请求的流水线是：

1. **Query Planning**（`query_planner.py`）：LLM-first planner（gpt-4o-mini）把自然语言问题解析成受约束的语义槽位（question_type、metric_keys、target_sections、time_scope、comparison_basis 等），输出经过 `PlanValidator` 严格校验——LLM 只能在白名单值里选择，返回非法字段直接拒绝并降级到 safe text plan。然后第二个 LLM 调用（dense query rewriter）基于已验证的槽位生成按角色分组的稠密检索查询。
2. **Bounded ReAct Agent**（`research_agent.py`）：一个有界的 ReAct 控制器，根据 plan 决定证据需求，循环执行工具动作（`query_xbrl_metrics`、`retrieve_mda`、`retrieve_risk_factors`、`retrieve_segment_discussion`、`retrieve_prior_filings`、`retrieve_filing_chunks`），每步观察证据计数并更新状态，满足证据角色要求或达到 max_steps（默认 5）即停止。
3. **Hybrid Retrieval**（`retrieval.py`）：每个工具动作内部是三路混合检索——pgvector 稠密检索 + Postgres 词法检索 + XBRL 事实查询，用加权 RRF 融合，再做元数据 rerank（章节匹配、表单类型、时间）。
4. **Evidence Pack 构建**：把 top 证据按角色分组（primary financial statements / MD&A / segment / risk factors / annual context），并抽取句级 evidence spans，生成 metric comparisons（YoY 等）。
5. **Answer Generation**（`answer_generation.py`）：LLM 在只包含证据对象的 prompt 上生成 JSON 格式回答，要求 `[evidence_id]` 引用标记。
6. **Citation Validation**：校验所有引用 ID 必须 ∈ prompt 证据集 ∩ allowed 集；失败时把错误回传给 LLM 重试一次，再失败降级到确定性的 extractive 生成器，最后兜底 insufficient_evidence。
7. **Research Run 打包与持久化**（`research_run.py`）：返回 `research_run.v1` 契约——answer、citations、validation、agent steps、normalized evidence、diagnostics（含每阶段耗时），前端渲染完整 trace。每个 run 同时落库到 `research_runs` 表（JSONB 全量 payload + 可索引的摘要列），`GET /research/runs/{run_id}` 可在任何时候取回完整审计记录，`GET /research/runs?ticker=` 列出历史 run——审计性是可回查的，不只是响应里的一次性展示。

---

## 二、Agent 设计

### Q3. 你的 "Agent" 是规则驱动的状态机，不是让 LLM 自由决策。为什么？这还算 Agent 吗？

**答：** 这是有意的设计取舍。`ResearchAgentService` 实现了 ReAct 的核心循环（thought → action → observation → 更新状态 → 决定下一步），但 next_action 的决策逻辑是确定性的，基于 plan 的语义槽位和当前证据状态标志（has_metric_evidence、has_mda_explanation 等）。

选择确定性控制器的理由：

1. **决策空间小且可枚举**：工具只有 6 个，证据角色需求由问题类型完全决定（risk 问题需要 Risk Factors，why 问题需要 metrics + MD&A）。让 LLM 每步做一次决策会增加 5 次串行 LLM 调用的延迟和成本，但决策质量不会更好——LLM 的判断力已经在 planner 阶段用过了，它输出的槽位就是 agent 的决策依赖。
2. **可测试、可回归**：确定性控制器可以用纯单元测试覆盖所有分支（`test_research_agent.py`），LLM 决策的 agent 行为不可复现。
3. **失败模式可控**：`evidence_enough()` 显式定义了"什么情况下证据足够"，stop_reason 只有三种（evidence_sufficient / max_steps_reached / insufficient_evidence），每种都有明确的下游处理。

我认为 "agentic" 的本质是**根据中间观察动态调整行为**，而不是"每步都问 LLM"。本系统满足：MD&A 检索没拿到 driver 证据时会追加 segment discussion 检索；XBRL 查到指标但没有可比前期时会触发 retrieve_prior_filings。如果未来问题类型扩展到决策无法枚举（如跨公司多跳推理），我会把 next_action 换成 LLM function-calling，但保留同样的状态、预算和 trace 结构——这个架构是为可替换设计的。

### Q4. Agent 失控怎么办？比如无限循环或不停调用工具？

**答：** 三层防护：

1. **步数预算**：`max_steps`（默认 5，配置上限 10），到达即强制 finalize，stop_reason 记为 max_steps_reached，用已有证据作答。
2. **动作去重**：`state.actions_taken` 集合保证每个工具动作最多执行一次，结构上不可能死循环。
3. **整体降级**：agent 路径整个包在 try/except 里，任何异常都会降级到非 agent 的 `_retrieve_planned` 静态检索路径，并在 trace 的 degraded 列表里记录 `react_agent_fallback:<异常类名>`，请求不会失败。

### Q5. 你怎么记录和展示 Agent 的推理过程？为什么不存完整 chain-of-thought？

**答：** 每一步存结构化的 step 记录：`thought_summary`（一句话的决策理由）、`action`、`action_input`、`observation_summary`、`evidence_ids`、`stop_reason`，由 `trace_payload()` 输出 `react_agent_trace.v1`，经 `research_trace.py` 转换成前端可渲染的 step 时间线。

不存完整 CoT 的原因：(1) 这里的"思考"是确定性逻辑，一句摘要就完整表达了决策依据；(2) trace 是要给用户看和长期存储的，摘要式 trace 信息密度高、体积小；(3) 即使将来换 LLM 决策，存决策摘要 + 输入输出也比存原始 CoT 更稳定——CoT 格式随模型版本漂移，结构化 trace 不会。

---

## 三、RAG 与检索

### Q6. 为什么要做三路混合检索（dense + lexical + XBRL facts），只用向量检索不行吗？

**答：** 三路各自覆盖对方的盲区：

- **Dense（pgvector，text-embedding-3-small）**：擅长语义改写——"经营业绩怎么样" 能命中 "results of operations"。但对精确数字、专有名词、表格内容召回差。
- **Lexical（Postgres 文本检索）**：擅长精确词命中——产品名、人名、会计术语。补 dense 的精确匹配短板。
- **XBRL facts**：财务数字的 ground truth。"Q2 revenue 是多少" 这种问题根本不该靠文本检索回答——文本里同一个数字有多种口径和单位写法，而 XBRL 是 SEC 强制公司提交的结构化数据，带期间、单位、tag 的规范化语义。

融合用**加权 RRF**（reciprocal rank fusion）：它只依赖排名而不依赖各路异质的原始分数（余弦距离 vs 词法得分不可比），对分数尺度不敏感，是多路融合最稳健的基线。之后用元数据 rerank 做业务感知调整：目标章节匹配、preferred form type、filing 时间新近度加 boost。每一步的 fusion_score、source_ranks、rerank_boosts 都进 trace，排序可解释、可调试。

### Q7. 你的 chunk 怎么切的？引用怎么做到页码级？

**答：** 用 `sec2md` 把 SEC filing HTML 解析成带章节结构和页码标注的 Markdown，chunk 沿章节边界切并保留 `(section_id, start_page, end_page, char offsets)` 元数据。引用的最小单位不是 chunk 而是 **evidence span**——evidence pack 构建时会在 chunk 内抽取与问题最相关的句级片段，span 的 evidence_id 编码了来源 chunk 和字符区间（如 `span:1279:mda_explanation_chunks:966:1052`），所以前端能高亮到句子级、链接到 SEC 原文页。

embedding 输入有版本号（`EMBEDDING_INPUT_VERSION`），检索时强制匹配 provider/model/dimensions/input_version 四元组，保证换 embedding 模板或模型后新旧向量不会混用——这是很多 RAG 系统忽略的静默 bug 来源。

### Q8. 检索质量怎么保证时间正确性？比如用户问"最新季度"，怎么不检索到旧 filing？

**答：** 两级机制：

1. **Planner 输出时间槽位**：time_scope（latest / comparison_trend / unspecified）、duration_class（quarter / ytd / fy / instant）、target_period，由 validator 校验并有默认推断规则。
2. **RetrievalScope 锚定**：如果 XBRL 指标查询先命中了具体 filing，`scope_from_metric_observations` 会用该 filing 的 accession number / filing_ids 反向约束文本检索范围，保证"数字证据"和"文字证据"来自同一期 filing，不会出现数字是 2026 Q2、解释是 2025 Q3 的错配。比较类问题（YoY）则显式生成 metric comparisons，当前期与前期各自携带来源 filing URL。

XBRL 的一个领域陷阱：US-GAAP 没有单独的 Q4 facts（只有 FY 和 Q1-Q3），系统里有专门的 `_computed_q4_from_fy_fact` 逻辑用 FY 减去前三季度推算 Q4，并把组成事实 ID 记进 component_fact_ids 保持可追溯。

---

## 四、幻觉控制与引用体系

### Q9. 你怎么防止 LLM 编造引用或编造数字？

**答：** 纵深防御，五层：

1. **Prompt 约束**：system prompt 要求只用 payload 里的证据对象作答，禁止外部事实，引用必须是 `[evidence_id]` 精确形式；同时禁止投资建议。
2. **引用规范化**（`normalize_generated_answer_citations`）：LLM 实际输出会出现 `[1]`、`[source #2]` 这类编号引用，系统维护 alias map 把它们映射回真实 evidence_id；映射不上的非法 marker 直接从答案文本里剔除。
3. **白名单校验**（`CitationValidator`）：所有引用 ID 必须同时属于 allowed_evidence_ids 和 prompt_evidence_ids（双集合交集），有效引用为零则校验失败。
4. **失败重试 → 降级链**：校验失败把错误结构化回传给 LLM 重试一次；再失败降级到 `ExtractiveAnswerGenerator`（确定性地把最强证据按优先级拼成带引用的句子，不可能幻觉）；extractive 也失败则返回 insufficient_evidence 兜底文案。
5. **数字来源隔离**：财务数字以 XBRL fact / metric comparison 证据对象的形式进 prompt（已格式化为 "$94.0B"），LLM 的任务是组织语言而不是计算数字，从源头降低数字幻觉面。

另外我最近把句级检查也接上了：`claim_citation_coverage` 用 `answer_claim_sentences` + `sentence_requires_citation` 检测每个论断句是否带有效引用，未引用的句子作为结构化 warning 进入 validation 结果和 eval 指标（不阻断回答，因为段落级引用可以支撑相邻句子，硬阻断会伤害可用性）。

### Q10. 为什么校验失败时返回 "insufficient evidence" 而不是尽力给个答案？

**答：** 这是产品层面的安全决策：在投研场景，**错误回答的代价远高于不回答**。一个带着权威引用外观但实际无据的回答，比"我没有足够证据"危险得多——用户会基于它做判断。所以失败路径的设计原则是 fail-safe：兜底回答明确说明没有足够已验证的证据，limitations 里写明原因（如 "Citation validation failed for the generated answer"），同时 retrieved_evidence_ids 照样返回，用户可以自己看检索到了什么。这也让系统行为可被 eval 断言：unanswerable 问题的期望输出就是 insufficient_evidence 状态，而不是某种碰运气的文本。

---

## 五、性能与延迟

### Q11. 这个系统一次回答要多久？瓶颈在哪？你做了什么优化？

**答：** 优化前端到端通常 10-25 秒；优化后实测冷启动约 19 秒、重复问题约 6.6 秒（其中检索全程仅 ~92ms，剩余基本是 answer LLM 生成本身）。原始串行链路分解（diagnostics 里 timing_ms 记录的）：

| 阶段 | 耗时来源 |
| --- | --- |
| Planner LLM | gpt-4o-mini 调用 #1 |
| Dense query rewriter LLM | gpt-4o-mini 调用 #2（依赖 #1 输出，无法并行） |
| Agent 工具步 ×N | 每步一次 embedding API 调用 + DB 全表向量扫描（最多 5 步） |
| Answer LLM | 最大的单段，输出长则更久；校验失败会再来一次 |

我做的优化（按实施顺序）：

1. **共享 OpenAI client**（`openai_client.py`，`lru_cache`）：原来 planner、rewriter、answer generator、embedding provider **每次调用都 new 一个 `OpenAI()` 客户端**，每次都重新 TLS 握手。改为按 (api_key, timeout, max_retries) 缓存复用，HTTP 连接池生效。
2. **LLM 响应缓存**：planner 和 rewriter 都是 temperature=0 的确定性调用，按 (model, question/payload) 做 LRU 缓存。重复问题的 planner 耗时从数秒降到 1ms。
3. **查询 embedding 缓存**：相同查询文本的向量按 (model, dimensions, text) 缓存（只缓存 ≤512 字符的查询文本，避免文档批量嵌入污染缓存），批量调用只对 miss 部分请求 API。
4. **输出 token 上限**：`ANSWER_LLM_MAX_OUTPUT_TOKENS`（默认 900）截断答案生成的长尾延迟。
5. **planner 与 rewriter 合并为单次 LLM 调用**：planner 的 system prompt 扩展为同时返回语义槽位和 `dense_query_specs`（few-shot 示例同步更新），inline specs 经过与独立 rewriter 完全相同的 `_validated_llm_dense_query_specs` 校验；校验不通过自动回退到原来的两次调用路径。冷启动直接省掉一次完整 LLM 往返。
6. **pgvector HNSW 索引**（migration 0008）：dense 检索从全表顺序扫描换成 HNSW 索引扫描（cosine ops 与 `<=>` 算子匹配），`ef_search` 按事务用 `set_config` 设置；`VECTOR_SEARCH_MODE=exact` 时显式禁用 index scan 保证精确语义，用完即恢复不影响同事务的词法查询。
7. **延迟进入 eval**：answer eval 的每个 case 可设 `max_duration_ms` 预算，延迟回归会让 eval 失败，性能不再只靠手感。

剩余瓶颈是 answer LLM 生成本身（约 6 秒，不可缓存），下一步是流式输出（感知延迟降一个数量级，但要解决"边流边展示未校验内容"的产品问题——方案是流式展示 + 末尾追加校验状态事件）和 async 执行模型（提吞吐）。

### Q12. 合并 planner 两次调用会改变输出分布，你怎么控制风险？

**答：** 三层控制。第一，**校验逻辑完全复用**：inline 返回的 dense specs 走的是与独立 rewriter 输出完全相同的 `_validated_llm_dense_query_specs` 白名单校验（角色合法性、文本长度、禁止编造数字等），单次调用没有获得任何"特权通道"。第二，**失败自动降级**：inline specs 缺失或校验不通过时，自动回退到原来的两次调用路径，matched_rules 里会记录走了哪条路（`dense_query:planner_single_call` vs `dense_query:llm_rewriter`），可观测可统计。第三，**工程顺序**：我先做了不改变输出语义的优化（连接复用、缓存、token 上限）并先建好 answer eval 回归护栏，然后才动这个质量敏感的部件。这是我做性能工程的一般原则：先收割零风险收益，同时建护栏，再做激进优化。

---

## 六、评估体系

### Q13. 你怎么评估这个系统的质量？RAG 系统的 eval 难在哪？

**答：** 分层评估，每层对应一个可独立失败的组件：

1. **Planner eval**（`query_planner_eval.py` + 测试集）：断言问题被解析成正确的槽位——question_type、metric_keys、时间口径。含歧义槽位专项集（`query_planner_ambiguous_slot_eval.json`）。
2. **Retrieval gold eval**（`retrieval_gold_eval.py`）：每个 case 给定问题和期望 evidence_ids，算 recall@final-pack，case 级 min_recall 阈值。种子集从真实 AAPL 10-Q 检索 dump 标注而来。
3. **Answer eval**（`answer_eval.py`，本次新增）：端到端跑 `/research/runs`，每 case 断言——validation_status（含 unanswerable 问题必须返回 insufficient_evidence）、最少引用数、**句级引用覆盖率**（min_claim_citation_coverage）、内容正则（must_match，如答案必须含 "$" 数字）、禁止模式（默认禁投资建议用语如 "price target" / "we recommend"）、延迟预算（max_duration_ms）。输出套件级指标：pass_rate、平均引用覆盖率、平均耗时。
4. **单元/集成测试**：266 个 pytest，所有 LLM 依赖通过 Protocol 注入 fake，CI 不需要 API key。

RAG eval 的难点及我的对策：(a) **标注贵** → gold set 刻意小而精，从真实检索 dump 里筛选标注，文件里写明"数据重新摄取后需刷新 ID"；(b) **答案没有唯一正确文本** → 不评字面相似度，评**可验证的结构性质**：引用有效性、覆盖率、关键内容模式、安全性质；(c) **LLM-as-judge 不稳定** → 现阶段全部用确定性断言，LLM judge（忠实度评分）列为下一步，且会以"判例 + 人工抽检校准"方式引入。

### Q14. 句级引用覆盖率为什么设计成 warning 而不是 error？

**答：** 因为它的假阳性代价不对称。段落级引用惯例下，一个句子没有自己的 marker 不代表无据——前一句的引用经常覆盖整段论述。如果做成 error，系统会把大量合格回答打回重试甚至降级，可用性受损；做成 warning + 计数（claim_sentence_count / cited_claim_sentence_count），它就成为一个**可监控、可设阈值的质量信号**：eval 里按 case 设 min coverage 阈值，生产环境可以看覆盖率分布的漂移。这体现一个评估设计原则：硬校验只放置信度高的规则（引用 ID 白名单），统计性信号走软指标。

---

## 七、数据与工程

### Q15. SEC 数据摄取有什么坑？你怎么处理限流和合规？

**答：** SEC EDGAR 有严格的访问规范：必须带标识身份的 User-Agent（含联系邮箱），速率上限 10 req/s。系统的 `sec_client.py` 实现了 User-Agent 强制校验（缺失直接 RuntimeError 拒绝启动请求）、令牌桶限流（配置上限锁死 10/s）、重试和失败处理；`sec_cache.py` 把 JSON 响应按 TTL（默认 24h）缓存在 Postgres，开发期反复摄取不会打 SEC。解析层面，filing HTML 极其不规范（嵌套表格、分页符、内联 XBRL），所以选了 `sec2md` 这种专门库而不是通用 HTML 解析，并保留 raw 和 annotated 两份文档缓存，解析逻辑升级后可离线重放，不需要重新下载。

### Q16. 数据库 schema 怎么设计的？为什么向量也放 Postgres 而不是专用向量库？

**答：** 核心表：companies → filings → filing_documents → document_chunks → chunk_embeddings，加 financial_facts（XBRL）、jobs、sec_response_cache，Alembic 管理迁移。

向量放 pgvector 的理由：(1) 证据检索需要**向量相似度 + 关系过滤（公司、form type、日期、章节）+ join 回 chunk 元数据**的一体化查询，专用向量库做过滤要么性能差要么要把元数据冗余同步过去；(2) 单库意味着事务一致性和一套运维；(3) HNSW 索引已实现（migration 0008，cosine ops），`VECTOR_SEARCH_MODE` 支持 hnsw（事务级 `ef_search` 预算）/ exact（显式禁用 index scan 保证精确）/ auto 三档，召回-速度权衡是显式可配的。如果走到亿级向量多租户，再迁专用向量库，但那时瓶颈和现在完全不同，提前迁移是过度设计。

### Q17. 测试策略是什么？LLM 依赖怎么测？

**答：** 266 个测试，几个关键做法：

1. **依赖倒置**：所有外部依赖定义为 Protocol（`LLMPlanner`、`AnswerGenerator`、`EmbeddingProvider`、`DenseQueryRewriter`），测试注入确定性 fake，CI 无 API key、无网络。
2. **分层测试**：模型层（schema/迁移）、服务层（planner 校验逻辑、agent 状态机全分支、citation 校验、Q4 推算等领域逻辑）、API 层（路由契约）、eval 工具自身也有测试（eval harness 用 fake runner 测断言逻辑）。
3. **降级路径显式测试**：LLM 失败 → extractive fallback、agent 异常 → 静态检索、embedding 缺失 → lexical-only，这些 degraded 分支都有专门用例——降级逻辑不测等于没有。
4. **配置默认值测试**：`test_config.py` 锁住 env 默认值，防止改配置时悄悄改变生产行为。

---

## 八、取舍、反思与扩展

### Q18. 如果重做一次，你会改什么？

**答：** 三件事：

1. **更早建立端到端 answer eval**。我先建了 planner 和 retrieval 的 eval，answer 层 eval 是后补的。结果是中途调 answer prompt 时只能手动看输出，效率低且无回归保护。教训：**端到端质量护栏应该在第一个能跑通的版本时就建**，哪怕只有 3 个 case。
2. **延迟预算应该从第一天就是显式约束**。系统是按"正确性优先"长出来的，串行 LLM 调用链是自然累积的结果。如果一开始就给"单次回答 P95 ≤ 10s"的预算，planner 两次调用合一、流式输出这些决策会更早发生。
3. **retrieval.py 该更早拆分**。4000 行单文件，agent 工具执行、候选源、融合 rerank、evidence pack 构建四块职责应该是四个模块。功能迭代速度掩盖了重构时机。

### Q19. 这个系统怎么扩展到生产规模——比如覆盖全部美股、多用户并发？

**答：** 按瓶颈出现顺序：

1. **摄取侧**：现在是按 ticker 手动触发的同步摄取。生产需要任务队列（已有 jobs 表打底）做全市场定期摄取，SEC 限流 10/s 是全局约束，需要中心化的限流器和优先级队列（新 filing 优先）。
2. **检索侧**：HNSW 索引已就位，规模化主要是按公司分区和 ef_search 召回调参（用 retrieval gold eval 做召回回归）。
3. **回答侧**：LLM 调用是吞吐瓶颈，方向是请求级并发（FastAPI async + 异步 OpenAI client）、流式输出、以及把 run 做成异步任务（提交即返回 run_id，前端轮询/订阅——research_run 契约已经按这个形状设计了）。
4. **质量运维**：eval 套件接入 CI 做发布门禁；生产侧按 validation_status、引用覆盖率、降级率、P95 延迟建监控面板——trace 里这些数据已经齐了，缺的只是聚合。

### Q20. 项目里你最得意的一个技术决策是什么？最不满意的呢？

**答：** 最得意的是**evidence_id 作为全系统的统一货币**。从检索、evidence pack、prompt、LLM 输出、校验、前端渲染到 eval 标注，所有环节都用同一套稳定 ID 衔接。这一个决策同时解锁了：引用可机器校验（防幻觉）、trace 可端到端关联（可调试）、gold set 可标注（可评估）、前端可高亮溯源（可用性）。好的中间表示比任何单点算法优化的杠杆都大。

最不满意的是**同步阻塞的执行模型**。所有 LLM/embedding/DB 调用都是同步串行，FastAPI 的 async 能力完全没用上。这是当初"先跑通再优化"的合理起点，但现在它是延迟和吞吐的共同天花板。已经做的连接复用和缓存是在这个模型内的优化，换异步执行模型是下一个大重构。

### Q21.（压力测试）有人质疑：这就是个 RAG 加几个 if-else，算什么 AI Agent 项目？你怎么回应？

**答：** 我会从三点回应：

1. **Agent 的判断标准是行为而不是实现**：系统具备目标分解（planner 槽位化）、工具选择与编排（6 个检索工具按证据状态动态调度）、中间结果反馈（observation 更新状态、MD&A 不足时自动追加 segment 检索）、预算与终止控制（max_steps、stop_reason）、自我修正（校验失败回传错误重试）——这是 agent 系统的完整闭环。决策器目前是确定性的，是因为在这个问题域它**更优**（可测、可复现、零额外延迟），不是因为做不了 LLM 决策。
2. **工程难点恰恰在 LLM 之外**：可验证引用体系、XBRL 期间语义（Q4 推算、口径对齐）、多级降级链、分层 eval——这些是把 demo 变成可信系统的部分，也是大多数 "调用一下 LangChain" 的项目缺失的部分。
3. **架构为演进而设计**：planner/agent/generator 全部是 Protocol 注入，把确定性 next_action 换成 LLM function-calling 不需要动检索、校验、trace 任何一层。我能清楚说出什么时候该换（决策空间无法枚举时）以及为什么现在不换——知道在哪里**不用** LLM，和知道在哪里用，是同一种工程判断力。

---

## 附录：高频快问快答

**Q: 为什么用 RRF 而不是训练一个 reranker？**
A: RRF 无需训练数据、对异质分数尺度免疫，是当前数据量下的正确基线。learned reranker 在 README 里列为后续优化，前提是 gold set 积累到足够规模。

**Q: 为什么 planner 的 LLM 输出还要过一层 validator？**
A: LLM 输出是不可信输入。validator 把它当用户输入一样做白名单校验（字段、枚举值、组合合法性），非法即降级。"LLM 提议，规则处置" 是整个系统对 LLM 输出的一致态度。

**Q: temperature 为什么全是 0？**
A: planner、rewriter、answer 三处都是结构化任务，要的是确定性和可缓存性，不是多样性。temperature=0 也让 LLM 响应缓存语义上成立。

**Q: 中英文问题都支持吗？**
A: 支持。planner 是 LLM-first，对中文问题天然鲁棒；agent 的 driver 判断里也显式处理了中文关键词（如"原因"）。

**Q: 如果 OpenAI 全挂了，系统行为是什么？**
A: 全链路降级但不死：planner → safe text plan（宽文本检索）；dense → 跳过（degraded 记录 missing_embeddings），lexical + XBRL 继续；answer → extractive 生成器。回答质量下降但仍然带引用、仍然不幻觉。

**Q: LLM 输出的引用格式不规范怎么办？**
A: 归一化层处理三类实际观察到的漂移：`[1]`/`[source #2]` 编号引用映射回真实 evidence_id；`[evidence_id: chunk:123]` 前缀变体剥离前缀；映射不上的非法 marker 从文本中清除。这些规则都来自真实运行中观察到的 LLM 行为，每类都有回归测试。

**Q: research run 存了什么？为什么存全量 JSONB 而不是规范化表？**
A: `research_runs` 表存可索引的摘要列（run_id、ticker、status、validation_status、duration_ms）+ 完整 `research_run.v1` payload 的 JSONB。审计场景的读模式是"按 run_id 取完整快照"，不是跨 run 的字段聚合查询，规范化反而把一个版本化契约拆散到多张表里、契约升级时要做多表迁移。JSONB + 契约版本号字段是审计快照的正确形态；将来要做聚合分析（如引用覆盖率趋势）再加物化视图。审计写入失败不会让请求 500——run 照常返回，只损失可回查性，并打 error 日志。

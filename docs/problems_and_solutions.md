# 项目问题与解决（面试用：遇到过什么问题，怎么解决的）

> 用法：面试官问“做这个项目遇到过什么问题、怎么解决的”，直接讲这里的任意一条。
> 每条结构固定：**改动前的问题 → 根因 → 怎么改的 → 改动后的表现（含实测）**。
> 模块对应真实代码：`backend/app/services/{research_agent,answer_generation,retrieval,research_run}.py`、
> `backend/app/api/routes/research.py`、`frontend/src/App.tsx`。

最适合开口的一条：**第 1 条（引用校验只验“指针”不验“语义”）**——它最能体现“先精确划定我保证了什么/没保证什么，再把没保证的补上”的成熟度。

---

## 1. 引用校验只验“引用完整性”，不验“语义蕴含”（最核心）

### 改动前的问题
项目的卖点之一是“反幻觉的引用校验”。但改动前 `CitationValidator` 只保证**引用是真的**，不保证**证据真的支持这句话**：
- 硬性 fail 只有两种：空答案 / 没有任何合法引用。
- 一句**编造的结论**只要挂一个**合法的引用 marker**（比如 “Apple 将停产 iPhone `[3]`”，而 `[3]` 是真实证据），就会 **passed，连 warning 都没有**。

一句话点破：**校验的是“引用完整性”（指针指向真实证据），不是“语义蕴含”（证据是否真的支持这句话）。我建好了第一种，没建第二种。**

### 根因
`validate()` 只做了三件事：引用 ID 必须 ∈ `allowed ∩ prompt` 集合、句级引用覆盖率（warning）、空答案检查。这些都是**结构性**检查，完全不看证据文本和句子的语义关系。

### 怎么改的（分两步堵两类漏洞）
两步都落在 [answer_generation.py](../backend/app/services/answer_generation.py) 的 `CitationValidator`：

**(a) 数字接地（先做的一步）** — `number_support_warnings`：
- 从答案里抽取**有金融含义的数字**（带 `$`、scale 词、`%`/`pp`），刻意忽略年份/计数以降误报。
- 和检索到的证据数字按容差对账（金额相对 1%、百分比绝对 0.3pt / 相对 3%），所以 `$111.18B → $111.2B` 这种四舍五入不会误报。
- 两类 **warning**：`unsupported_number`（任何证据都不支持 = 幻觉）、`citation_number_mismatch`（数字存在但不在本句所引证据里 = 张冠李戴）。

**(b) 语义蕴含（这次补的关键一步）** — 可注入的 `EntailmentJudge` / `LLMEntailmentJudge`：
- 对每个“需要引用且确实带了引用”的句子，把（句子 + 它所引证据文本）批量交给一次 LLM 判定，返回 `entailed / neutral / contradicted`。
- 映射：`contradicted → error`（进入 fail→重试→extractive 兜底链，会拦下答案）；`neutral → warning`（`unsupported_claim`，不拦）。
- 工程取舍：judge 是**可注入**的（测试注入假 judge，离线确定性）；用 `answer_entailment_check` **默认关闭**——它是验证热路径上的额外 LLM 调用，按环境 `ANSWER_ENTAILMENT_CHECK=true` 开启，和代码库对其它 LLM 组件“测试注入、生产开”的惯例一致；judge 任何异常都**降级为不报问题**，不会因为校验器不可用而拖垮一次正常回答。

### 改动后的表现
- 实测（真 LLM）：把“Apple 计划明年停产 iPhone `[fact]`”判为 **contradicted**，把“营收约 $111B `[fact]`”判为 **entailed**——编造结论现在会被拦下，而不是干净通过。
- 单测覆盖：contradicted → `status=failed` 且含 `contradicted_claim`；neutral → `passed` 且含 `unsupported_claim` warning；entailed → 干净；关闭开关时整段跳过。
- 现在能诚实地说两个硬保证：①每个引用都指向真实证据；②每个金融数字都和证据在容差内对过账；③（开启时）每个带引用的论断都过了一次蕴含判定。

---

## 2. “ReAct agent” 其实是规则驱动的状态机

### 改动前的问题
简历写的是 ReAct agent，但 `next_action()` 是一整段 `if/elif` 级联：**循环内一次 LLM 都不调**，`thought_summary` 是写死的模板字符串，observation 只翻转布尔 flag 喂给人写的决策树。被懂行的面试官一问“这还算 ReAct 吗”就站不住——它是**确定性状态机**，不是 ReAct。

### 根因
工具选择逻辑是手写规则。LLM 只在进循环前的 planner 用过一次，循环内的“推理”全是硬编码分支。

### 怎么改的
[research_agent.py](../backend/app/services/research_agent.py)：
- 新增 `AgentReasoner` Protocol + `LLMAgentReasoner`：**每一步**把 question / plan / 已收集证据 / 历史 observations 喂给 LLM，让它返回 `{thought, action}`（`json_object` 模式，复用 planner 的缓存与 JSON 解析）。
- `next_action` 委托给可注入的 reasoner，**删掉整段 if/elif**；`max_steps` 仍作硬上限，`evidence_enough`/limitations 降级为给 LLM 的提示 + trace 标注。
- 关键省力点：检索工具的参数本就由 `plan + 动作名` 推导、`action_input` 被忽略，所以**只换“决策”、不动“执行”**——retrieval.py 的工具层零改动。
- 安全网：LLM 决策失败时走**已有的** `_retrieve_planned` 静态兜底，不留任何规则 ReAct 代码。trace `mode` 改为 `react_llm`。

### 改动后的表现
- 实测：对“Apple 毛利率为何变化”问题，LLM 首选 `query_xbrl_metrics`，`thought` 是真实推理（“先取 XBRL 结构化数据理解数字变化”），再 `retrieve_mda → retrieve_segment_discussion`，最后 `finalize_answer`。trace 里的 thought 是模型真实输出，不是模板。
- 代价（诚实说）：每步多一次 controller LLM 调用，冷态延迟上升（见第 5 条 benchmark）；暖态因决策缓存几乎归零。这是 LLM-in-the-loop 的预期成本，换来真实的自适应工具选择。

---

## 3. 流式输出“先展示后撤回”

### 改动前的问题
流式端点把**校验前的答案草稿**逐 token 推给前端打字机显示，最后再用校验后的答案**整体替换**。投研场景下不可接受：一个会被判 failed（甚至幻觉）的答案，在被拦下前用户已经一个字一个字读到了、甚至可能截了图。

### 根因
把“展示活跃度”和“展示答案”耦合在了一起——为了低感知延迟，直接流答案 token，而校验只能在答案生成完之后做。

### 怎么改的
解耦两者：
- 后端 [answer_generation.py](../backend/app/services/answer_generation.py)：`_generate` 不再转发 `answer_delta`，改发一个 `status`(answering) 事件；内部仍可流式但**不外发未校验 token**。校验后的答案通过终态 `run` 事件揭晓。
- 前端 [App.tsx](../frontend/src/App.tsx)：删掉打字机草稿面板与相关状态/CSS，`LiveRunProgress` 只渲染阶段 pill + agent 步骤列表；答案只在 `run` 事件到达后渲染。

### 改动后的表现
- 实测事件序列：`['status','step','step','step','step','step','status','validation','run']`——**完全没有 `answer_delta`**，以 `run` 收尾。
- 用户看到的每个字都过了校验闸门；活跃感由 status/step 事件维持。一句话：**流式只改“何时展示已校验内容”，不再展示未校验内容。**

---

## 4. 流式端点的并发隐患（断连不取消 + Session 跨线程）

### 改动前的问题
两个真实隐患：
1. **断连不取消**：客户端断开后 worker 仍在跑，继续烧 LLM token，还会落一条没人看的 run。
2. **Session 跨线程**：请求级 SQLAlchemy `Session` 在请求线程创建，却被丢进 `run_in_executor` 的 worker 线程使用——Session 非线程安全，靠“恰好只有一个 worker 碰它”维系，很脆。

### 怎么改的
[research.py](../backend/app/api/routes/research.py) 的 `stream_research_run`：
- **协作取消**：用 `threading.Event` 作 `should_cancel` 贯穿 `ResearchRunService.run`——agent 每步之间、答案生成之前检查；事件流里用 `http_request.is_disconnected()` 轮询，断连即 set 取消并 break。取消时 `run()` 在落库前抛 `RunCancelled`，worker 吞掉、不落库。
- **每路新建 session**：worker 内用 `get_sessionmaker()` 开一个**绑定到 worker 线程**的 session（`with ... as db:`），不再复用请求级 session。

### 改动后的表现
- 单测验证：`should_cancel` 为真时 `run()` 抛 `RunCancelled` 且**不写库**（无孤儿 run）。
- 浪费的工作被限制在“至多一次在途 LLM 调用”——因为 Python 杀不掉运行中线程，取消是协作式的，到下一个检查点才停。

---

## 5. 延迟：先测量再优化，打真正的长杆（第一/二轮）

### 改动前的问题
端到端 10–25s，但没人知道时间花在哪。

### 根因 + 怎么改的
先在 `retrieval_trace.timing_ms` 埋点，定位到**瓶颈是 answer LLM 的逐 token 解码**（检索仅几十到几百 ms）。然后只打长杆：
- 共享 OpenAI client（`openai_client.py`，`lru_cache`）——原来每次调用都新建 client + 重新 TLS 握手。
- planner / rewriter 的 LLM 响应 LRU 缓存（temperature=0 确定性，可缓存）+ 查询 embedding 缓存。
- `ANSWER_LLM_MAX_OUTPUT_TOKENS`（默认 900）截断答案长尾——**真正命中瓶颈的优化**（输出 token 数主导解码时长）。
- planner + dense rewriter **合并为单次 LLM 调用**（inline `dense_query_specs`，校验失败回退两次调用）——砍掉关键路径上一次串行往返。

### 改动后的表现（这次在 4 家公司真实数据上重测）
真 ReAct 版本、AAPL/MSFT/NVDA/TSLA × metric/why/risk、各冷/暖一次（共 12 次，全部 completed/passed）：

| 场景 | 平均总时长 | 检索 | 答案 LLM |
|---|---|---|---|
| 冷（首次/缓存未命中） | ~12.7s | 6.7s | 6.0s |
| 暖（planner/agent决策/embedding 命中缓存） | ~6.0s | 0.3s | 5.7s |
| **总体平均** | **~9.4s** | — | — |

要点：**暖态检索掉到 ~0.3s**（缓存有效），瓶颈仍是答案 LLM 解码（暖态几乎全是它）；真 ReAct 的 per-step controller LLM 调用主要体现在**冷态检索**里，暖态因决策缓存归零。诚实限定：样本量小、答案 LLM 解码尾延迟波动大（曾见单次 16s 离群），所以报区间而非单点。

### 诚实归因（别吹错地方）
**HNSW 索引在当前 ~1.6 万向量上省的延迟≈0**（flat 搜索本就百毫秒级）。简历里把它归为**“扩展准备”**，不是“延迟优化”——否则被问“省了多少 ms”就崩。

---

## 6. 引用 marker 变体导致合法答案被误判（修 bug）

### 改动前的问题
LLM 实际会输出 `[1]`、`[source #2]`、`[evidence_id: chunk:123]` 等**引用变体**，而校验器只认精确的 `[evidence_id]`，导致带合法引用的答案被判“无有效引用”而误降级。

### 怎么改的
`normalize_generated_answer_citations`：维护 alias map 把编号引用映射回真实 evidence_id、剥离 `evidence_id:` 前缀、剔除映射不上的非法 marker。每类漂移都来自真实运行观察，配回归测试。

### 改动后的表现
带变体引用的答案现在能正确通过校验；归一化层是“LLM 提议、规则处置”态度的一部分。

---

## 7. 已知边界（面试主动说，反而加分）

这些是**当前没保证的**，能划清边界比吹全能更可信：
- **answer eval 验“形状”不验“对错”**：`must_match` 只验格式（有 `$` 数字、带引用、够快、无投资建议），不验数值真伪；gold 集偏 AAPL、且从自身检索 dump 反向种（只抓回归）。便宜的补法：warning 进 gate、断言真值、跨 ticker gold。
- **evidence pack 每步全量重建**仅为拿 counts（N+1），应改增量。
- **同步阻塞执行模型**：LLM/embedding/DB 全同步串行，规模化要换异步执行模型 / 任务队列。

---

## 速记：一条都能独立讲的“问题→解决→效果”

1. 引用校验只验指针不验语义 → 加数字接地 + claim 级蕴含校验 → 编造结论被判 contradicted 拦下。
2. 伪 ReAct（if/elif）→ 换成每步 LLM 选工具的真 ReAct（可注入、带静态兜底）→ trace 里是真实推理，工具序列自适应。
3. 流式先展示后撤回 → 解耦“流进度 vs 流答案”，校验后才揭晓 → 事件流无 answer_delta，用户只见已校验内容。
4. 断连不取消 + Session 跨线程 → 协作取消 + 每路新建 session → 取消不落库、线程安全。
5. 延迟没头绪 → 埋点定位到答案 LLM → 共享 client/缓存/token 上限/合并 planner → 暖态检索 0.3s、总体均值 ~9.4s。

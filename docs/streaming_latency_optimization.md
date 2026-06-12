# Answer 生成提速：流式输出改造全记录（面试叙事版）

> 本文档记录 2026-06-12 在 `feat/streaming-research` 分支上完成的第三轮延迟优化：
> 为什么原版本慢、瓶颈如何定位、做了哪些改动、每个设计决策的取舍、实测效果。
> 配合 `docs/interview_qa.md` 使用。涉及的代码：
> `backend/app/api/routes/research.py`、`backend/app/services/answer_generation.py`、
> `backend/app/services/research_run.py`、`backend/app/services/retrieval.py`、
> `backend/app/services/research_trace.py`、`frontend/src/api/sec.ts`、`frontend/src/App.tsx`。

---

## 一、改造前的状态：为什么慢

### 1.1 原版本的请求生命周期（全同步、全阻塞）

改造前，前端点击 "Ask Question" 后调用 `POST /research/runs`，后端在**一个同步函数里跑完整条流水线，直到最后一步才返回任何字节**：

```
HTTP 请求进入
  └─ QueryPlanner.plan()            ← planner LLM 调用（gpt-4o-mini，有 LRU 缓存）
  └─ ReAct agent 检索循环            ← 3 路混合检索 × 最多 5 步（纯 DB + pgvector）
  └─ OpenAIAnswerGenerator.generate() ← answer LLM 调用，stream=False，JSON mode
  └─ CitationValidator.validate()    ← 引用校验（纯 CPU，毫秒级）
  └─ （校验失败则重试 LLM 一次，再失败降级 extractive）
  └─ 落库 research_runs 表
HTTP 响应返回 ← 用户在这之前看到的是一个转圈的按钮
```

用户的**感知延迟 = 整条流水线的总时长**。前端只有一个 `fetch`，期间没有任何中间反馈——既不知道系统在做什么，也不知道还要等多久。

### 1.2 瓶颈定位：靠数据，不靠猜

前两轮优化已经在 `retrieval_trace.timing_ms` 里埋了每阶段耗时（这是面试中值得强调的习惯：**先可观测，再优化**）。实测数据（AAPL，warm 缓存）：

| 阶段 | 耗时 | 占比 |
|---|---|---|
| planner（LRU 缓存命中后） | < 1 ms | ~0% |
| ReAct 检索全流程（XBRL + 稠密 + 词法 + RRF 融合 + rerank + evidence pack） | ~92–180 ms | < 3% |
| **answer LLM 生成（非流式 + JSON mode）** | **~6.5 s（基线日）/ ~20 s+（API 慢的日子）** | **> 95%** |
| 引用校验 + 落库 | ~10 ms | ~0% |

结论非常清晰：**检索侧已经被前两轮优化解决了（共享 OpenAI client、planner/rewriter 合并单次调用 + LRU 缓存、查询 embedding 缓存、pgvector HNSW 索引），剩下的延迟几乎全部是 answer LLM 的生成时间**。继续优化检索是在 3% 上做文章，必须动 answer 生成。

### 1.3 answer LLM 为什么这么慢：三个独立的原因

**原因 1：非流式——必须等最后一个 token 才能拿到第一个字节。**

LLM 是自回归解码：输出的每个 token 都要做一次完整的 forward pass，输出 token 数量基本决定生成时长（输入 token 走并行 prefill，相对便宜）。`stream=False` 时，OpenAI 服务端要等全部 ~300+ 个输出 token 生成完毕才返回响应。也就是说：

> 就算总生成时间不变，非流式把"逐渐变好"的过程压成了"全有或全无"——
> 用户感知延迟被人为放大到最差情况。

**原因 2：JSON mode 的结构性开销。**

原实现要求模型返回 JSON 对象（`response_format={"type": "json_object"}`）：

```json
{
  "answer": "Apple's revenue grew ... [metric_comparison:revenue:...] ...",
  "citations": ["metric_comparison:revenue:...", "span:101:primary_financial_statement_chunks:0:80", ...],
  "limitations": ["..."]
}
```

这个设计有两个隐藏的 token 浪费：

1. **citations 数组是纯冗余输出**。answer 字符串里已经内嵌了 `[evidence_id]` 引用标记，而 `CitationValidator` 本来就会用正则从 answer 里提取 marker（`extract_citation_markers()`），两者取并集。evidence_id 很长（如 `span:101:primary_financial_statement_chunks:0:80`，单个 ≈ 15–25 个 token），citations 数组等于**把每个引用 ID 完整输出第二遍**。5–8 个引用就是 100–200 个白白多等的 token。
2. **JSON 转义和包装本身**：answer 作为 JSON 字符串值，内部的引号、换行都要转义，外层还有 key、引号、括号。

此外 JSON mode 在 OpenAI 侧走受约束解码，同等内容的生成速度通常不快于纯文本（实测见第四节，差距相当明显）。

**原因 3：架构上"校验优先"与"尽早展示"的矛盾没有解。**

之前没做流式不是疏忽，而是有一个真实的设计顾虑（前两轮的遗留 TODO 里写的是"answer 流式输出——需解决未校验内容先展示的问题"）：本项目的核心卖点是**引用校验**，校验发生在答案生成完之后；如果把 token 直接流给用户，等于把未经校验的内容先展示了。这个矛盾不解决，流式就动不了。本轮的核心设计就是解这个矛盾（见 2.4）。

---

## 二、做了什么改动

### 2.1 总体方案：一个新的流式端点 + 事件协议

新增 `POST /research/runs/stream`，以 **NDJSON**（newline-delimited JSON，每行一个 JSON 事件）流式返回；原 `POST /research/runs` 同步端点**原样保留**（evals、既有测试、可能的程序化调用方都依赖它的 JSON 契约）。

事件协议（按时间顺序）：

| 事件 | 含义 | 触发时机 |
|---|---|---|
| `{"type":"status","stage":"planning",...}` | 阶段提示 | 请求一进来立即发出（覆盖 planner LLM 的等待期） |
| `{"type":"step","step":{...}}` | 一条 agent 步骤（结构同 `research_run.v1` 的 step） | ReAct 循环每完成一步实时发出 |
| `{"type":"answer_started","attempt":n}` | 某次答案生成开始 | 每次调 LLM 前；前端据此**清空草稿**（重试场景） |
| `{"type":"answer_delta","text":"..."}` | 答案文本增量 | OpenAI 流式 chunk 到达时 |
| `{"type":"validation","status":"passed/failed"}` | 引用校验结果 | 每次校验后 |
| `{"type":"run","run":{...}}` | **完整的校验后 run**（与旧端点同构） | 流水线结束，作为终止事件 |
| `{"type":"error","message":"..."}` | 错误（公司不存在等） | 异常时，作为终止事件 |

**为什么是 NDJSON 而不是 SSE 或 WebSocket？**
- WebSocket 是双向通道，这里只需要单向推送，引入它要多管理连接生命周期，过度设计。
- SSE（`text/event-stream`）的原生客户端 `EventSource` 只支持 GET，而这里需要 POST + JSON body，反正都要用 `fetch` + `ReadableStream` 手动解析，SSE 的 `event:/data:` 帧格式就只剩格式负担。
- NDJSON 一行一个 JSON，后端 `yield json.dumps(event) + "\n"`，前端按 `\n` 切分 `JSON.parse`，是两边都最简单的协议。

### 2.2 同步流水线如何流式化：线程 + asyncio.Queue 桥接

整条 pipeline（SQLAlchemy 同步 Session、同步 OpenAI client）都是同步代码，而 FastAPI 的 `StreamingResponse` 需要异步生成器。改造**没有重写 pipeline 为 async**（那是大手术，且同步 DB driver 决定了收益有限），而是用标准的桥接模式（`research.py`）：

```python
queue: asyncio.Queue = asyncio.Queue()
loop = asyncio.get_running_loop()

def emit(event):                       # 在工作线程里被调用
    loop.call_soon_threadsafe(queue.put_nowait, event)   # 线程安全地投递到事件循环

def execute():                         # 同步 pipeline，跑在线程池里
    run = ResearchRunService(db).run(request, on_event=emit)
    emit({"type": "run", "run": run.model_dump(mode="json")})

async def event_stream():              # 异步生成器，喂 StreamingResponse
    worker = loop.run_in_executor(None, execute)
    while True:
        event = await queue.get()
        yield json.dumps(event, ensure_ascii=False) + "\n"
        if event.get("type") in {"run", "error"}:
            break
    await worker
```

要点：pipeline 在 executor 线程里跑并通过 `emit` 回调发事件；`call_soon_threadsafe` 是跨线程往 asyncio 队列投递的正确方式；异步生成器从队列消费并逐行 yield，HTTP chunked 编码把每行实时推到客户端。

### 2.3 事件从哪里来：回调贯穿三层

改动刻意保持"**回调可选、默认行为不变**"——`on_event=None` 时所有代码路径与改造前完全一致，这也是旧端点和全部既有测试不受影响的原因：

1. **`RetrievalService.retrieve(request, *, on_agent_step=None)`**（`retrieval.py`）：ReAct 循环在 `agent.start()`、每次 `agent.observe()`、`agent.finish()` 之后调用回调，把刚追加的原始 step dict 交出去。改动只有 ~10 行。
2. **`ResearchRunService.run(request, *, on_event=None)`**（`research_run.py`）：把原始 agent step 转成 `ResearchRunStepRead`（复用从 `build_research_run_steps` 抽出来的 `agent_step_to_run_step()`，保证流式步骤和最终 run 里的步骤**同构**），包装成 `step` 事件。
3. **`ResearchAnswerService.answer_from_retrieval_response(..., *, on_event=None)`**（`answer_generation.py`）：在生成-校验-重试循环里发 `answer_started` / `answer_delta` / `validation` 事件。

### 2.4 核心改动：流式生成放弃 JSON mode，换纯文本协议

这是**真实提速**（不只是感知提速）的关键，也是含金量最高的部分。

**新的输出协议**（`answer_stream_system_prompt()`）：模型直接输出带 `[evidence_id]` 标记的纯文本答案；如有 limitations，在末尾用哨兵行分隔：

```
Apple's revenue grew significantly ... [metric_comparison:revenue:q_latest_vs_prior_year] ...
LIMITATIONS:
- Only one quarter of segment detail was available.
```

为什么可以安全地扔掉 JSON：

- `citations` 数组本来就是冗余的——校验器一直都在用 `extract_citation_markers()` 从 answer 文本提取引用，流式路径直接只用 marker 提取，**输出 token 直接少一截**。
- `limitations` 用哨兵行 `LIMITATIONS:` + 逐行 `- ` 表达，`split_streamed_answer()` 一个正则就能解析。
- 两套 prompt（JSON 版给旧路径、纯文本版给流式路径）共享同一个 `ANSWER_PROMPT_CORE`（引用规则、回答风格、合规约束），只有输出格式段不同，避免规则漂移。

**一个值得在面试里讲的工程细节：`AnswerStreamEmitter` 的 holdback 机制。**
哨兵 `LIMITATIONS:` 可能被 token 边界切开（一个 delta 是 `"\nLIMIT"`，下一个才是 `"ATIONS:"`）。如果把 delta 原样转发，用户会先看到半截哨兵、然后整个 limitations 块闪现在答案区。解法：emitter 始终**扣住缓冲区尾部 24 个字符不发**，每次新 delta 到达后先在全缓冲区里搜哨兵——找到就停止对外发射（limitations 部分只进缓冲区、不进前端）；流结束时如果从未出现哨兵，把扣住的尾巴一次性放出。约 25 行代码，配了独立单元测试（哨兵跨 delta 切分的用例）。

**生成器的分层流式支持**：
- `OpenAIAnswerGenerator.generate_stream()`：`stream=True` 真流式；
- `ExtractiveAnswerGenerator.generate_stream()`：生成后单次全量 delta（它本来就是毫秒级的确定性兜底）；
- `FallbackAnswerGenerator.generate_stream()`：主生成器抛 `AnswerGenerationError` 时无缝切到兜底，limitations 里追加降级说明；
- 自定义/测试注入的生成器没有 `generate_stream` 也没关系——`generate_with_optional_stream()` 检测能力，不支持就退化为"生成完一次性发出"。

### 2.5 解掉历史矛盾："未校验内容先展示"怎么办

这是之前两轮一直不敢做流式的原因，本轮的答案是**把流式文本定位成"草稿"，把校验后的最终结果定位成"唯一事实"**：

1. 流式期间前端渲染的是 draft：引用标记显示为不可点击的顺序编号 chip（`[1]` `[2]`），UI 上有明确的"Writing cited answer…"进行中状态，用户不会把它当成定稿；
2. 流结束后，校验、normalize（修复 `[1]`/`[evidence_id: ...]` 等引用变体、剔除非法 marker）照常执行——**校验逻辑一行没改**；
3. 终止事件 `run` 携带校验后的完整答案 + 结构化 citations，前端**整体替换**草稿，引用变成可点击、可滚动定位的卡片；
4. 如果第一次生成校验失败，重试前会发新的 `answer_started` 事件，前端清空草稿重新流——用户看到的是"系统发现引用问题，正在重写"，这本身就是可解释性的一部分；
5. 最坏情况降级到 extractive 或 insufficient_evidence，与旧路径完全一致。

一句话总结这个设计：**流式只改变"何时让用户看到内容"，不改变"什么内容算数"。** 校验语义零妥协。

### 2.6 前端改动

- `sec.ts`：`streamResearch()` 用 `fetch` + `response.body.getReader()` 读流，`TextDecoder` 解码、按行切分、`JSON.parse` 后回调；`ResearchStreamEvent` 是 discriminated union，事件处理用类型收窄。
- `App.tsx`：新增 `liveStage` / `liveSteps` / `liveAnswer` 三个状态；`LiveRunProgress` 组件复用既有的 `answer-panel` / `trace-step` 样式体系渲染：阶段 pill（Planning retrieval → Gathering evidence → Writing cited answer → Validating citations）、已完成步骤列表 + 虚线脉冲的 "IN PROGRESS" 项、打字机式答案（带闪烁光标）；尾部未闭合的半截 `[marker` 用一行正则 `replace(/\[[^\]]*$/, "")` 隐藏。
- `run` 事件到达后 `setResearchRun(run)`，渲染条件切换，live 面板被完整结果替换。

---

## 三、为什么不是别的方案

面试里大概率被问"你还考虑过什么"，如实回答：

| 备选方案 | 为什么没选 |
|---|---|
| 换更小/更快的模型 | answer 模型已经是 gpt-4o-mini（该系列最快档）；再降模型答案质量先受损，且不解决"全有或全无"的感知问题 |
| 降低 `ANSWER_LLM_MAX_OUTPUT_TOKENS` | 已经做过（前一轮加的配置，默认 900）；继续压会砍掉 5–8 句的分析师风格回答，属于拿质量换延迟 |
| pipeline 内并行 | 没有可并行的结构：检索必须在生成前，校验必须在生成后，串行是本质的 |
| 全面 async 重写 | 收益集中在高并发吞吐而非单请求延迟；同步 SQLAlchemy + 线程桥接已经够用；列为后续方向 |
| 预生成/缓存答案 | 对 demo 有效但对真实使用无意义（问题空间开放）；且已有的 planner LRU 缓存覆盖了可缓存的部分 |
| 推测解码/自建推理 | 用的是 OpenAI API，没有控制推理栈的空间 |

流式 + 协议精简是唯一同时改善**感知延迟**（首 token 时间）和**真实延迟**（输出 token 减少 + 摆脱 JSON mode）且不牺牲答案质量、不动校验语义的方案。

---

## 四、实测效果

测试条件：AAPL，"Why did revenue grow last quarter?"，本地 Postgres（2254 条 embeddings），2026-06-12 实测。当天 OpenAI API 整体偏慢（旧路径实测 21–25s，而历史基线日是 ~6.6s），所以**关键是同时段的相对对比**：

| 指标 | 旧：`/research/runs`（非流式 + JSON mode） | 新：`/research/runs/stream` |
|---|---|---|
| 用户看到第一个有意义的反馈 | 全程无反馈，直到结束 | **~40 ms**（status 事件 + live 面板） |
| agent 检索步骤可见 | 结束后一次性出现 | **0.07–0.19 s** 内逐条实时出现 |
| 答案首 token | = 总时长（21–25 s） | **1.5–3.1 s** |
| 总时长（同时段对比） | 21.6 s / 25.4 s（两次） | 10.9 s / 12.4 s（两次） |
| 引用校验 | passed | passed（语义不变） |

要点表述（面试时的一句话版本）：

> 首个有效反馈从 20 多秒降到 40 毫秒，答案首 token 从"等于总时长"降到 1.5–3 秒；
> 同时因为流式协议扔掉了 JSON mode 和冗余的 citations 数组输出，
> 同时段对比下**总时长也降到原来的一半左右**（~11s vs ~22s）。
> 历史基线日（API 正常时非流式 ~6.6s）下推算，纯文本流式的总时长收益会小一些，
> 但首 token 的感知收益是结构性的，不依赖 API 当天的状态。

诚实的限定（说出来反而加分）：总时长减半的对比样本量小（各 2–3 次）、两种协议生成的答案长度不完全相同（996–1233 字符 vs 1059 字符）、且当天 API 波动大，"JSON mode 慢约一倍"不宜当成普适结论；但方向上与原理一致（更少的输出 token + 无受约束解码）。

另外的冷启动观察：进程冷启动后的第一次请求 ~30s（planner LLM 冷连接 11.6s + 首次 answer 调用），第二次起 planner 命中 LRU 缓存 < 1ms——这印证了前一轮做 LLM 响应缓存的价值。

---

## 五、测试与回归保障

- 新增 9 个后端测试：`split_streamed_answer` 解析（含无 limitations 用例）、`AnswerStreamEmitter` 哨兵跨 delta 切分与无哨兵 flush、`ResearchAnswerService` 事件序列（`answer_started → answer_delta → validation`）、`ResearchRunService` 完整事件流（含 step 事件转换）、流式端点 NDJSON 契约（终止于 `run` 事件、`contract_version` 校验）、流式端点错误事件（公司不存在 → `error` 事件而非 500）。
- 全量 283 个测试通过；旧端点契约零改动（`on_event=None` 路径与改造前逐行等价）。
- 端到端验证用 Playwright 驱动真实浏览器：确认 live 面板 40ms 内出现、步骤逐条渲染、draft 从 34 字符增长到 800+、最终被校验后结果替换；并探测了连续二次提问（状态重置干净）和空问题（客户端拦截）两条异常路径。

---

## 六、面试可能的追问（速查）

**Q：流式输出会不会把幻觉/坏引用直接漏给用户？**
A：不会改变最终交付物。草稿仅用于进行中状态的展示（带明确的 in-progress UI），校验、引用 normalize、失败重试、extractive 兜底全部保留；终止事件携带的是校验后答案，前端整体替换草稿。校验失败重试时 `answer_started` 事件会让前端清空草稿，用户能直观看到"系统在自我纠错"。

**Q：为什么不用 WebSocket / SSE？**
A：单向推送 + 需要 POST body。WebSocket 多余（双向、连接管理成本）；原生 SSE 客户端不支持 POST，既然必须 fetch+ReadableStream 手动解析，NDJSON 比 SSE 帧格式更简单。

**Q：同步 pipeline 怎么接到异步响应上的？**
A：`run_in_executor` 把同步 pipeline 放进线程池，回调里用 `loop.call_soon_threadsafe(queue.put_nowait, event)` 跨线程投递到 `asyncio.Queue`，异步生成器消费队列逐行 yield。pipeline 一行业务逻辑都没重写。

**Q：纯文本协议下，citations / limitations 的结构化信息丢了吗？**
A：没有。citations 本来就有第二个来源（answer 内嵌的 `[evidence_id]` marker，校验器一直以它为准）；limitations 用哨兵行协议表达，解析是一个 multiline 正则。JSON 版 prompt 仍服务于旧端点，两套 prompt 共享同一个规则核心。

**Q：怎么保证流式路径和旧路径不漂移？**
A：三点——共享 `ANSWER_PROMPT_CORE`；流式 step 事件和最终 run 的 steps 用同一个转换函数 `agent_step_to_run_step()`；终止 `run` 事件直接复用 `ResearchRunRead`（`research_run.v1` 契约），前端最终渲染代码完全复用。

**Q：下一步还能怎么提速？**
A：(1) 句级增量校验——在流式过程中对已完成的句子提前跑 marker 校验，把"重试"提前发现；(2) async 执行模型 + 连接池调优，主要收益在并发吞吐；(3) 答案分段生成（先 takeaway 后展开），进一步压首句完整时间；(4) 冷启动预热（进程启动时预建 OpenAI 连接）。

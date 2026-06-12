# 代码改动完全讲解（2026-06-11 两轮优化）

> 目的：让你能对照源码理解每一处改动，并在面试中讲清楚"为什么这么改"。
> 每节结构：**原来的问题 → 改动位置 → 代码怎么工作 → 设计取舍 → 面试追问预演**。
> 建议读法：开着对应源文件，按本文顺序过一遍，每节花 5-10 分钟。

---

## 总览：改了哪些文件

```
新增文件
├── backend/app/services/openai_client.py          # 改动1: 共享 OpenAI 客户端
├── backend/app/evals/answer_eval.py               # 改动6: 端到端 answer eval
├── backend/evals/answer_gold_eval.json            # 改动6: eval 测试集
├── backend/app/models/research_run.py             # 改动8: run 持久化模型
├── backend/alembic/versions/0007_create_research_runs.py   # 改动8: 建表迁移
├── backend/alembic/versions/0008_chunk_embeddings_hnsw.py  # 改动9: HNSW 索引迁移
├── backend/tests/test_answer_eval.py              # 测试
└── backend/tests/test_perf_caches.py              # 测试

修改文件
├── backend/app/services/embedding_provider.py     # 改动3: 查询 embedding 缓存
├── backend/app/services/query_planner.py          # 改动2: LLM 响应缓存 + 改动10: 单次调用合并
├── backend/app/services/answer_generation.py      # 改动4: token上限 + 改动5: 句级覆盖 + 改动7: 引用变体修复
├── backend/app/services/research_run.py           # 改动8: 持久化逻辑
├── backend/app/services/retrieval.py              # 改动9: vector_search_mode 生效
├── backend/app/core/config.py                     # 改动4/9: 新配置项
├── backend/app/schemas/answer.py                  # 改动5: validation 新字段
├── backend/app/schemas/research_run.py            # 改动8: 摘要 schema
└── backend/app/api/routes/research.py             # 改动8: GET 端点
```

一句话版本：**第一轮解决"慢"和"没有质量度量"，第二轮解决"审计是空的"、"向量检索是全表扫描"、"LLM 引用格式漂移"，并完成第一轮承诺的 planner 合并。**

---

# 第一轮：延迟 + 评估

## 改动 1：共享 OpenAI 客户端

**文件：** `backend/app/services/openai_client.py`（新增，约 25 行）

### 原来的问题

打开改动前的代码（git diff 可见），四个地方各自都是这个模式：

```python
# 旧代码：query_planner.py、answer_generation.py、embedding_provider.py 里各有一份
client = OpenAI(
    api_key=api_key.get_secret_value(),
    timeout=self._settings.query_planner_llm_timeout_seconds,
    max_retries=...,
)
```

`OpenAI()` 构造的是一个全新的 HTTP 客户端，**每次请求都要重新建 TCP 连接 + TLS 握手**（几百毫秒）。一次 `/research/runs` 要打 4-8 次 OpenAI API，等于白付 4-8 次握手成本。

### 改动内容

```python
# openai_client.py 全部核心代码
@lru_cache(maxsize=8)
def get_openai_client(
    api_key: str,
    *,
    timeout: float | None = None,
    max_retries: int = 2,
) -> "OpenAI":
    from openai import OpenAI
    return OpenAI(api_key=api_key, timeout=timeout, max_retries=max_retries)
```

然后把四处 `client = OpenAI(...)` 全部替换为 `client = get_openai_client(api_key, timeout=..., max_retries=...)`。

### 怎么工作

- `@lru_cache` 按参数元组 `(api_key, timeout, max_retries)` 缓存返回值。第一次调用创建客户端，之后**相同参数直接返回同一个对象**。
- OpenAI SDK 的客户端内部持有 httpx 连接池，复用同一个客户端 = 复用底层 TCP/TLS 连接。
- `maxsize=8`：planner（timeout=20s）、answer（timeout=30s）、embedding（无 timeout）参数组合不同，会各占一个缓存槽——这正是为什么**不能用全局单例**，因为不同调用方需要不同的超时预算。
- `from openai import OpenAI` 放在函数体内（延迟导入）：保持原代码"openai 包未安装时优雅报错"的行为——调用方用 `try/except ImportError` 包住。

### 设计取舍

| 备选方案 | 为什么没选 |
| --- | --- |
| 全局单例 client | 不同调用方需要不同 timeout/max_retries |
| 每个 Service 持有一个 client 实例 | Service 本身是每个请求新建的（`RetrievalService(db)`），client 还是会被反复创建 |
| 依赖注入容器 | 项目没有 DI 框架，引入一个框架来解决连接复用是杀鸡用牛刀 |

### 面试追问预演

- **Q: lru_cache 线程安全吗？FastAPI 是并发的。**
  A: CPython 的 `lru_cache` 本身线程安全（内部有锁）；OpenAI 官方客户端也明确文档化为线程安全。最坏竞态是两个线程同时 miss 各创建一个 client，其中一个被缓存——浪费一个对象，无正确性问题。
- **Q: api_key 是明文进缓存 key 的，有风险吗？**
  A: key 只存在进程内存里，和 Settings 里的 SecretStr 解包后传给 SDK 是同一个生命周期，没有新增暴露面（不落日志、不序列化）。
- **Q: 为什么不顺手改成 AsyncOpenAI？**
  A: 整个服务层是同步的，单点换异步客户端没有意义，async 化是一个独立的大重构（见"未实施"清单）。

---

## 改动 2：Planner / Rewriter 的 LLM 响应缓存

**文件：** `backend/app/services/query_planner.py` 顶部（约第 12-48 行）+ `LLMQueryPlanner.plan_candidate` + `LLMDenseQueryRewriter.rewrite`

### 原来的问题

planner 和 dense rewriter 都是 `temperature=0` 的**确定性**调用——相同输入理论上产出相同输出。但每次请求都重新调 API，重复问题白白付出 2 次串行 LLM 往返（2-5 秒）+ 费用。

### 改动内容

模块顶部加了一个手写的 LRU 缓存（`OrderedDict` + `Lock`）：

```python
LLM_RESPONSE_CACHE_MAX_ENTRIES = 256
_llm_response_cache: OrderedDict[tuple[str, str, str], Any] = OrderedDict()
_llm_response_cache_lock = Lock()

def _llm_cache_get(key):           # 命中时 move_to_end —— LRU 的"最近使用"语义
def _llm_cache_put(key, value):    # 超过 256 条时 popitem(last=False) 淘汰最旧
def clear_llm_response_cache():    # 测试用
```

`plan_candidate` 里的接入点（注意看缓存 key 和存取时机）：

```python
cache_key = ("plan_candidate", self._settings.query_planner_llm_model, question)
cached = _llm_cache_get(cache_key)
if cached is not None:
    return json.loads(cached)          # ① 命中：跳过整个 LLM 调用
...
# LLM 调用 + 字段白名单校验之后：
_llm_cache_put(cache_key, json.dumps(candidate, ensure_ascii=False))  # ②
return candidate
```

rewriter 的 key 是 `("dense_query_rewrite", model, payload_text)`，其中 `payload_text` 是发给 LLM 的完整 JSON payload（加了 `sort_keys=True` 保证 key 顺序稳定，否则同样的 payload 因 dict 顺序不同会算成不同 key）。

### 三个值得注意的细节（面试可以主动讲）

1. **缓存 key 包含 model 名**：换模型后旧缓存自动失效，不会拿 gpt-4o-mini 的结果冒充新模型的结果。
2. **存 JSON 字符串而不是 dict**：`json.loads` 每次返回**新对象**。如果直接存 dict，调用方拿到引用后修改它（planner 下游会处理 candidate），会污染缓存里的"原本"。这是防御性拷贝。
3. **缓存放在校验之后**：只有通过字段白名单校验的 candidate 才会被缓存，坏响应不会被固化。

### 为什么手写 LRU 而不是 functools.lru_cache？

`lru_cache` 装饰在方法上会把 `self` 也算进 key——而 `LLMQueryPlanner` 每个请求都新建实例，缓存永远 miss。手写模块级缓存把"实例"从 key 中剥离，只按 (用途, 模型, 输入) 缓存。

### 面试追问预演

- **Q: temperature=0 真的保证确定性吗？**
  A: 不严格保证（GPU 浮点非确定性、模型版本更新），但这恰好说明缓存是安全的：我们缓存的是"某次合法输出"，它过了校验就是可用的 plan，不要求是"唯一正确输出"。
- **Q: 多进程部署（gunicorn 多 worker）下这个缓存还有效吗？**
  A: 每个进程一份，命中率按 worker 数稀释。规模化方案是换 Redis 共享缓存，接口不变只换存储——这也是为什么 get/put 封装成函数而不是直接操作 dict。
- **Q: 缓存失效策略？财报更新了怎么办？**
  A: planner 的输出只依赖**问题文本**（它输出的是语义槽位，不是数据），财报更新不影响 plan 的正确性，所以不需要 TTL。真正依赖数据的环节（检索）没有缓存。

---

## 改动 3：查询 Embedding 缓存

**文件：** `backend/app/services/embedding_provider.py`

### 原来的问题

agent 每个检索步骤都调一次 `embed_texts`。同一问题重复跑、或者多步中出现相同查询文本时，相同文本被反复嵌入。embedding 调用一次 100-300ms。

### 改动内容

结构和改动 2 类似（`OrderedDict` + `Lock` 的模块级 LRU），但有两个针对 embedding 场景的特殊设计：

```python
QUERY_EMBEDDING_CACHE_MAX_ENTRIES = 512
QUERY_EMBEDDING_CACHE_MAX_TEXT_CHARS = 512   # ← 关键：只缓存短文本

def _cacheable(text: str) -> bool:
    return len(text) <= QUERY_EMBEDDING_CACHE_MAX_TEXT_CHARS
```

**为什么限制 512 字符？** 因为 `embed_texts` 有两类调用方：
- 检索时嵌入**查询**（短文本，少量，高重复率）→ 值得缓存
- 摄取时嵌入**文档 chunk**（`chunk_embeddings.py` 批量调用，长文本，几乎不重复）→ 缓存它们只会把有价值的查询缓存挤出去（缓存污染）

长度判断是区分两类调用最简单的启发式，不需要改调用方签名。

`embed_texts` 的主体改成了 **"部分命中"模式**——这是本改动最值得讲的代码：

```python
results: dict[int, list[float]] = {}
miss_indexes: list[int] = []
for index, text in enumerate(texts):
    cached = _cache_get(...) if _cacheable(text) else None
    if cached is not None:
        results[index] = cached          # ① 命中的直接用
    else:
        miss_indexes.append(index)       # ② 没命中的记下位置

if miss_indexes:
    response = client.embeddings.create(
        input=[texts[i] for i in miss_indexes],   # ③ 只把 miss 的发给 API
        ...
    )
    # ④ 按 index 排序对齐，写回 results 并入缓存
return [results[index] for index in range(len(texts))]   # ⑤ 恢复原始顺序
```

比如 agent 第 1 步嵌入了 `["query a"]`，第 2 步要嵌入 `["query a", "query b"]`——API 只会收到 `["query b"]`。（`tests/test_perf_caches.py::test_embed_texts_only_requests_cache_misses` 验证的就是这个行为。）

还加了一个防御：API 返回的向量数和 miss 数不一致时抛 `EmbeddingProviderError`，防止错位的向量被错误缓存。

### 面试追问预演

- **Q: 缓存 key 为什么是 (model, dimensions, text) 而不带 provider？**
  A: 当前缓存在 `OpenAIEmbeddingProvider` 类内部使用，provider 隐含为 openai。严格说加上更稳——这是一个可以承认的改进点。
- **Q: 512 条 × 1536 维 float 占多少内存？**
  A: 约 512 × 1536 × 8B ≈ 6MB，可忽略。这也是为什么敢用进程内缓存。

---

## 改动 4：Answer LLM 输出 Token 上限

**文件：** `backend/app/core/config.py` + `backend/app/services/answer_generation.py`

### 改动内容（很小但值得说清楚）

```python
# config.py 新增
answer_llm_max_output_tokens: int = Field(default=900, ge=64, le=4096)

# answer_generation.py 的 chat.completions.create 调用新增一行
max_tokens=self._settings.answer_llm_max_output_tokens,
```

### 为什么

答案规格是 5-8 句话（system prompt 里写明的），正常 300-500 tokens。但没有上限时模型偶尔会写很长，**生成时间和输出长度成正比**，这就是长尾延迟。900 给了 2 倍安全余量。

### 风险与对冲（面试必问）

**Q: 如果答案真的被截断了怎么办？输出是 JSON，截断 = JSON 不完整。**
A: `parse_generated_answer` 会抛 `AnswerGenerationError`（"invalid JSON"），走既有的降级链：重试一次 → extractive 生成器 → insufficient_evidence 兜底。**截断不会产生残缺答案给用户，只会触发降级**——这就是为什么这个改动是安全的：失败路径早已存在且被测试覆盖。

---

## 改动 5：句级引用覆盖率（Claim-level Citation Coverage）

**文件：** `backend/app/schemas/answer.py` + `backend/app/services/answer_generation.py`

### 原来的问题

`CitationValidator` 只检查两件事：答案非空、**至少有一个**有效引用。也就是说一个 8 句话的答案只要有 1 句带引用就算 passed——其余 7 句是否有据无人知晓。代码里其实早就写好了两个辅助函数 `answer_claim_sentences()`（把答案切成论断句）和 `sentence_requires_citation()`（判断句子是否需要引用），**但从未被接进校验**。README 的 "Not implemented" 里也承认了这点（"claim-level support validation"）。

### 改动内容

**(a) schema 加字段**（`schemas/answer.py`）：

```python
class CitationValidationRead(BaseModel):
    ...
    errors: list[CitationValidationIssueRead] = ...      # 原有：致命问题
    warnings: list[CitationValidationIssueRead] = ...    # 新增：质量信号
    claim_sentence_count: int = 0                        # 新增：分母
    cited_claim_sentence_count: int = 0                  # 新增：分子
```

**(b) 新函数 `claim_citation_coverage`**（answer_generation.py，约第 965 行）：

```python
def claim_citation_coverage(answer, *, valid_evidence_ids):
    for sentence in answer_claim_sentences(answer):       # 复用已有的切句逻辑
        if not sentence_requires_citation(sentence):      # 复用已有的"需要引用吗"判断
            continue
        claim_count += 1
        sentence_ids = set(extract_citation_markers(sentence))
        if sentence_ids & valid_evidence_ids:             # 句内 marker ∩ 有效证据集
            cited_claim_count += 1
        else:
            warnings.append(...code="uncited_claim_sentence"...)
```

**(c) 接入 validator**（`CitationValidator.validate` 末尾）：

```python
warnings, claim_count, cited_claim_count = claim_citation_coverage(
    generated.answer,
    valid_evidence_ids=allowed_set & prompt_set,   # 注意：是两个集合的交集
)
return CitationValidationRead(
    status="failed" if errors else "passed",       # ← status 只由 errors 决定，warnings 不影响
    ...
)
```

`allowed_set & prompt_set` 的含义：一个引用要"有效"，必须既在检索系统认可的证据集（allowed）里，又真的进了 prompt（prompt）——引用了一个没发给 LLM 的证据 ID 等于幻觉。

### 最重要的设计决策：为什么是 warning 不是 error？

因为**段落级引用惯例**下，"这句没有自己的 marker"不等于"这句无据"——前一句的引用经常覆盖整段。如果做成 error：
- 大量合格回答会被打回重试 → 多一次 LLM 调用 → 延迟翻倍
- 重试还不过 → 降级到 extractive → 答案质量反而变差

做成 warning + 计数后，它变成一个**可监控的质量指标**：eval 里可以设阈值（改动 6 的 `min_claim_citation_coverage`），生产环境可以看覆盖率分布漂移。原则一句话：**硬校验只放高置信度规则（引用 ID 白名单），统计性信号走软指标。**

实测验证：真实跑一次 AAPL 营收问题，validation 返回 `claim_sentence_count: 5, cited_claim_sentence_count: 3`，2 条 warning——信号确实在工作。

### 面试追问预演

- **Q: 切句准确吗？"$1.5 billion" 里的句号会误切吗？**
  A: `SENTENCE_BOUNDARY_RE = (?<!\d)[.!?](?!\d)` 用负向断言排除了数字中间的句号；`consume_following_citation_markers` 保证句尾的 `[marker]` 归属当前句而不是下一句。这两个细节是原作者写好的，我复用而非重写——读懂存量代码再扩展，比平行造轮子好。

---

## 改动 6：端到端 Answer Eval

**文件：** `backend/app/evals/answer_eval.py`（新增）+ `backend/evals/answer_gold_eval.json`（新增）

### 原来的问题

已有 planner eval（槽位对不对）和 retrieval gold eval（召回够不够），但**没有任何东西度量最终答案的质量**。改 answer prompt 全靠肉眼看输出。

### 设计：评什么、不评什么

LLM 答案没有唯一正确文本，所以**不评字面相似度**，只评**确定性的、可断言的结构性质**。每个 case 支持六种检查：

```jsonc
{
  "id": "aapl_latest_quarter_revenue",
  "ticker": "AAPL",
  "question": "What was Apple's revenue in the latest quarter?",
  "expect_validation_status": "passed",       // ① 校验状态（unanswerable 问题应是 insufficient_evidence）
  "min_citations": 1,                         // ② 最少引用数
  "min_claim_citation_coverage": 0.6,         // ③ 句级覆盖率下限（用的就是改动5的字段）
  "must_match": ["\\$\\d"],                   // ④ 内容正则：答案必须出现 $ 数字
  "max_duration_ms": 30000                    // ⑤ 延迟预算：性能回归会让 eval 失败
}                                             // ⑥ must_not_match + 内置禁止模式
```

内置禁止模式（所有 case 自动生效，对应"不做投资建议"的产品红线）：

```python
DEFAULT_FORBIDDEN_PATTERNS = [
    r"(?i)\bprice target\b",
    r"(?i)\bwe recommend\b",
    r"(?i)\b(buy|sell|hold)\s+(rating|recommendation)\b",
]
```

### 代码结构刻意复制 retrieval_gold_eval.py

`run_eval_file() / evaluate_case() / format_eval_result() / _json_result() / main()` 的骨架、CLI 参数（`--json`、`--max-failures`、`--no-fail-on-mismatch`）、退出码语义全部对齐既有 eval。**一个项目里第二个同类工具应该让人觉得"和第一个是同一个人写的"。**

一个关键接口设计：

```python
class ResearchRunner(Protocol):
    def run(self, request: RetrievalRequest) -> ResearchRunRead: ...

def run_eval_file(eval_file, *, db=None, runner=None):
    active_runner = runner or ResearchRunService(session)
```

`runner` 可注入 → `tests/test_answer_eval.py` 用 FakeRunner 返回构造好的 `ResearchRunRead`，**测的是断言逻辑本身**，CI 不需要数据库和 API key。真实运行（CLI）则连真库跑全链路。

还有一个防御细节：`ResearchRunRead.validation` 类型是 `CitationValidationRead | dict`，所以取覆盖率前先 `coerce_validation()` 统一成模型对象，否则 dict 形态会 AttributeError。

### 面试追问预演

- **Q: 为什么不用 LLM-as-judge 评忠实度？**
  A: LLM judge 自身不稳定（同输入不同分）、有成本、且需要先用人工标注校准它。先用确定性断言建立可信基线，LLM judge 作为下一层加在上面（评"引用的证据真的支持这句话吗"这类正则做不了的判断）。
- **Q: 5 个 case 太少了吧？**
  A: 是种子集。eval 基础设施的价值在于**让加 case 的边际成本趋近于零**——加一个 case 就是往 JSON 里写一个对象。刻意小而精也和项目里 retrieval gold eval 的哲学一致（文件头注释写明"intentionally small"）。

---

# 第二轮：诊断出的新问题 + 承诺的下一步

## 改动 7：引用 Marker 变体归一化（修 bug）

**文件：** `backend/app/services/answer_generation.py`

### 这个 bug 是怎么发现的（面试时是个好故事）

诊断时看 `git diff` 发现你**前端**有一处未提交的改动：把 marker 正则改成兼容 `[evidence_id: chunk:123]`。这说明 LLM 在真实运行中会输出这种带前缀的引用格式。但问题在于：**后端**的 `EVIDENCE_MARKER_RE` 只认 `[chunk:123]`，于是：

1. `extract_citation_markers` 抓不到这个引用 → 校验认为"没有有效引用" → `missing_valid_citations` → 触发不必要的 LLM 重试甚至降级；
2. `remove_invalid_citation_markers` 也匹配不到它 → 这串残缺文本原样留在答案里。

前端补丁只解决了"显示"，没解决"校验误判"。**修复应该在数据上游（后端归一化层），前端兜底治标不治本。**

### 改动内容

(a) 新增一个专门匹配前缀变体的正则（约第 41 行）：

```python
PREFIXED_EVIDENCE_MARKER_RE = re.compile(
    r"\[\s*evidence_id\s*:\s*((?:chunk|span|financial_fact|metric_observation|metric_comparison):[^\]\s]+)\s*\]",
    re.IGNORECASE,
)
```

(b) 在 `normalize_generated_answer_citations` 的**最前面**先做前缀剥离（顺序重要——后续的编号引用修复、非法 marker 清除都假设 marker 已是标准形）：

```python
answer = PREFIXED_EVIDENCE_MARKER_RE.sub(
    lambda match: f"[{match.group(1)}]",      # [evidence_id: chunk:5] → [chunk:5]
    generated.answer,
)
```

(c) `resolve_citation_reference`（处理 LLM 返回的 citations **数组**，不是正文 marker）同样剥前缀：

```python
cleaned = re.sub(r"(?i)^evidence_id\s*:\s*", "", cleaned)
```

### 归一化层现在处理的三类真实漂移（可以背下来）

| LLM 实际输出 | 处理 |
| --- | --- |
| `[1]`、`[source #2]` | alias map 映射回真实 evidence_id（原有逻辑） |
| `[evidence_id: chunk:123]` | 剥前缀（本次新增） |
| `[chunk:999]`（不在 prompt 证据集里） | 从文本中清除（原有逻辑） |

设计哲学：**对 LLM 输出做 Postel's Law——接收时宽容（归一化各种变体），校验时严格（白名单一票否决）。** 宽容层每条规则都来自真实观察到的失败案例，并各有回归测试（`test_generated_answer_normalization_repairs_evidence_id_prefixed_markers`）。

---

## 改动 8：Research Run 持久化（审计闭环）

**文件：** `models/research_run.py` + `alembic/versions/0007` + `services/research_run.py` + `schemas/research_run.py` + `api/routes/research.py`

### 原来的问题（诊断中最严重的一个）

旧的 `ResearchRunService.run()` 生成 `run_id = f"run_{uuid4().hex}"`，组装完整的 `research_run.v1` 响应——然后**什么都不存**。run_id 随响应返回后即成死数据：没有表、没有 `GET /research/runs/{id}`。README 宣称 "auditable research-run"，但"可审计"的最低要求是**事后可回查**，这个宣称当时是空的。面试官真去 curl 一下就会戳穿。

### (a) 表设计（`models/research_run.py`）

```python
class ResearchRunRecord(Base):
    __tablename__ = "research_runs"
    id = Column(Integer, primary_key=True)
    run_id = Column(String(64), unique=True, index=True)   # 对外的稳定标识
    ticker = Column(String(16), index=True)                # ┐
    question = Column(Text)                                # │ 摘要列：列表页和过滤用，
    status = Column(String(32), index=True)                # │ 不用解开 JSONB 就能查
    validation_status = Column(String(32))                 # ┘
    duration_ms = Column(Float, nullable=True)
    payload = Column(JSONB, ...)                           # 完整 research_run.v1 快照
    created_at = Column(DateTime(timezone=True), server_default=text("now()"))
```

**核心决策：摘要列 + JSONB 全量快照，而不是把 steps/evidence/citations 规范化成多张表。**
理由：审计的读模式是"按 run_id 取**完整快照**"，不是跨 run 的字段级聚合。`research_run.v1` 是一个**版本化契约**，拆成多表意味着契约每次演进都要做多表迁移；JSONB + payload 里自带的 `contract_version` 字段，旧 run 永远能按当时的版本读回。摘要列覆盖列表页需求（按 ticker 过滤、看状态和耗时）。将来真要做聚合分析（如引用覆盖率周趋势）再加物化视图。

风格对齐：JSONB、`server_default=text("now()")`、命名全部照抄 `Job` 模型的写法。

### (b) 迁移（`0007_create_research_runs.py`）

照抄 0006 的文件结构（revision 字符串、`upgrade()/downgrade()` 对称）。已在本地 Postgres 实际执行通过。

### (c) 服务层（`research_run.py`——重点读 `_persist_run`）

```python
def _persist_run(self, run: ResearchRunRead) -> None:
    if self._db is None:        # 测试常用 db=None 构造服务，跳过持久化
        return
    record = ResearchRunRecord(..., payload=run.model_dump(mode="json"))
    try:
        self._db.add(record)
        self._db.commit()
    except Exception:
        # An audit-write failure must not turn a completed run into a 500
        logger.exception("Failed to persist research run %s", run.run_id)
        self._db.rollback()
```

**最值得讲的决策：审计写入失败时怎么办？** 两个选项：

- 抛异常 → 用户视角：花了 19 秒、答案已经算出来了，却收到 500。
- 吞掉 → 审计记录丢一条。

选了后者 + `logger.exception` + rollback。理由：这是**研究助手**不是交易系统，答案的价值 > 单条审计记录；且失败有日志可告警。如果是合规强制审计的场景（如券商），决策会反过来——**能说出"什么场景下我会做相反决策"，比决策本身更展示判断力。**

`rollback()` 不可省略：commit 失败后 session 处于 invalid 状态，不回滚的话同一请求后续任何 DB 操作都会连环报错。

新增的两个读方法：`get_run(run_id)`（`select ... where run_id`，payload 直接 `model_validate` 回 `ResearchRunRead`）和 `list_runs(ticker=, limit=)`（按 `id desc` 排序，ticker 可选过滤）。

### (d) 路由（`api/routes/research.py`）

```python
@router.get("/runs", response_model=list[ResearchRunSummaryRead])   # 列表：只返回摘要列
@router.get("/runs/{run_id}", response_model=ResearchRunRead)      # 详情：完整契约，404 if missing
```

注意 FastAPI 路由顺序：`GET /runs`（无参数）必须能和 `GET /runs/{run_id}` 区分——FastAPI 按声明顺序匹配，`/runs` 先声明所以 `GET /research/runs` 不会被当成 `run_id=""`。

### (e) 测试（`tests/test_research_run_service.py` 新增三个）

沿用项目惯例：**不连真库，用 FakeSession**。`FakePersistenceSession` 实现 `add/commit/rollback` 并记录调用，`fail_commit=True` 模拟写库失败：

- `test_research_run_service_persists_run_record`：跑完 run 后 record 进了 session、字段正确、payload 里有 contract_version；
- `test_research_run_service_survives_persistence_failure`：commit 抛错后 run 照常返回 completed 且 rollback 被调用——**降级路径必须有测试，否则等于没有**；
- `test_research_run_service_get_run_round_trips_payload`：存进去的 payload 能完整读回成相同的 run。

---

## 改动 9：pgvector HNSW 索引 + vector_search_mode 做实

**文件：** `alembic/versions/0008_chunk_embeddings_hnsw.py` + `core/config.py` + `services/retrieval.py`

### 原来的问题

`chunk_embeddings` 表只有 B-tree 元数据索引，**embedding 列上没有任何向量索引**。每次 dense 检索（`ORDER BY embedding <=> query LIMIT 40`）都是全表顺序扫描逐行算余弦距离。`VECTOR_SEARCH_MODE` 配置存在但任何代码都没读过它——是个空壳。

### 背景知识（面试必须会讲）

- **HNSW**（Hierarchical Navigable Small World）：近似最近邻（ANN）图索引。把"算所有向量的距离再排序"（O(n)）变成"在多层跳表式的图上贪心导航"（近似 O(log n)）。代价：**近似**——可能漏掉真正的 top-k 中的个别项（召回损失）。
- **ef_search**：查询时维护的候选队列大小。越大→搜得越广→召回越高→越慢。这是 HNSW 的核心运行时旋钮。
- pgvector 中：表上建了 HNSW 索引后，`ORDER BY embedding <=> x LIMIT k` 形态的查询 Postgres 规划器会自动用索引。

### (a) 迁移 0008

```python
op.execute("""
    CREATE INDEX IF NOT EXISTS ix_chunk_embeddings_embedding_hnsw
    ON chunk_embeddings
    USING hnsw (embedding vector_cosine_ops)
""")
```

**`vector_cosine_ops` 必须与查询算子匹配**：检索 SQL 用的是 `<=>`（余弦距离），如果建成 `vector_l2_ops`（对应 `<->`），索引会完全不被使用——这是 pgvector 最常见的坑。m / ef_construction 用 pgvector 默认值（16/64），2254 行的数据量没必要调。

### (b) 配置（config.py）

```python
vector_search_mode: Literal["exact", "hnsw", "auto"] = "hnsw"   # 默认从 exact 改为 hnsw
hnsw_ef_search: int = Field(default=80, ge=10, le=1000)          # 新增
```

ef_search=80 而 dense 候选上限是 40，2 倍余量，小语料下召回损失可忽略。

### (c) retrieval.py 的接线（重点读这两个方法）

```python
def _apply_vector_search_mode(self, degraded) -> None:
    mode = self._settings.vector_search_mode
    if mode == "hnsw":
        self._db.execute(
            text("SELECT set_config('hnsw.ef_search', :ef_search, true)"),
            {"ef_search": str(self._settings.hnsw_ef_search)},
        )
    elif mode == "exact":
        self._db.execute(text("SELECT set_config('enable_indexscan', 'off', true)"))
    # auto: 什么都不做，让 Postgres 规划器自己选

def _restore_vector_search_mode(self) -> None:
    if self._settings.vector_search_mode != "exact":
        return
    self._db.execute(text("SELECT set_config('enable_indexscan', 'on', true)"))
```

调用点在 `_dense_candidate_sources`：apply 之后用 **try/finally** 包住整个 dense 查询循环，finally 里 restore。

四个值得讲的细节：

1. **为什么用 `set_config(..., true)` 而不是 `SET LOCAL hnsw.ef_search = 80`？**
   Postgres 的 `SET` 语句**不支持绑定参数**，要拼 SQL 字符串（注入风险+丑）。`set_config()` 是普通函数，支持参数绑定；第三个参数 `true` = 事务内有效（等价于 SET LOCAL），事务结束自动还原。
2. **exact 模式为什么要禁 index scan？** 这是语义契约问题：迁移 0008 建了索引之后，规划器会自动用它——如果 exact 模式什么都不做，"exact" 就**静默变成了近似搜索**，配置项变成谎言。`enable_indexscan=off` 强制回到顺序扫描，保住 exact 的字面含义（这也是 pgvector 官方文档教的强制精确搜索方法）。
3. **为什么要 restore？** `set_config(..., true)` 的作用域是**整个事务**，而同一个请求的 lexical 检索、chunk 加载在同一事务里——不恢复的话它们也被迫放弃各自的 B-tree 索引，整个请求变慢。try/finally 保证 dense 循环中途 return/抛异常也会恢复。
4. **失败降级**：set_config 抛异常（比如连的是不支持的库）不会让检索失败，只往 `degraded` 列表里记一条 `vector_search_mode_unavailable:<异常类>` —— 和项目里所有降级的处理风格一致。

### (d) 验证方式（面试可以复述）

对真实库跑 `EXPLAIN SELECT ... ORDER BY embedding <=> ... LIMIT 10`，输出 `Index Scan using ix_chunk_embeddings_embedding_hnsw` —— 证明索引真的被用上，而不只是"建了"。单元测试用 FakeSession 捕获执行的 SQL 文本，断言三种 mode 各自发出/不发出 set_config（`tests/test_retrieval.py` 末尾三个 test）。

---

## 改动 10：Planner + Dense Rewriter 合并为单次 LLM 调用

**文件：** `backend/app/services/query_planner.py`（这是两轮中逻辑最复杂的改动）

### 原来的链路 vs 新链路

```
旧（两次串行 LLM 调用）:
  问题 → [LLM#1 planner] → 槽位 → validator 校验 → [LLM#2 rewriter] → dense specs → 合并

新（一次调用 + 自动回退）:
  问题 → [LLM#1 planner] → 槽位 + dense_query_specs(inline)
            ├─ inline specs 过校验 → 直接用，省掉 LLM#2          ← 快路径
            └─ inline specs 缺失/无效 → 走旧的 LLM#2 路径          ← 回退
```

两次调用为什么以前是串行的：rewriter 的输入依赖 planner 的**已校验**槽位，没法并行。所以唯一的省法就是合并。

### 改动点逐个看

**(1) 字段白名单放行**（约第 125 行）：

```python
VALID_LLM_PLAN_FIELDS = {
    ...
    "dense_query_specs",  # 单次调用模式下随槽位一起返回的 dense 检索 query；无效时回退到独立 rewriter 调用。
}
```

planner 对 LLM 输出做字段白名单校验（出现未知字段直接拒绝整个响应），所以新字段必须先注册。对应改了一个旧测试：`test_llm_query_planner_rejects_unknown_fields_from_raw_response` 原来断言 `dense_query_specs not in allowed_fields`（旧契约），现在断言 `in`（新契约）——**契约测试跟着契约改，这是合理的测试更新，不是为了让测试变绿而改测试**。

**(2) Planner system prompt 扩展**（`_llm_planner_system_prompt`）：

- 删掉了原来的禁令"Do not output ... dense_query_specs ..."；
- 新增 "Dense query specs" 段：把原来 rewriter prompt 里的角色规则（什么情况下要 slot / financial_statement / cash_flow / liquidity / mda / risk）和文本规则（8-32 词、英文、不编造数字、比较类问题要带期间语言）**内联**进 planner prompt——因为单次调用时 LLM 看不到独立 rewriter 那份 prompt 了；
- 两个 few-shot 示例（performance_overview 和 risk）补上了 `dense_query_specs` 输出，让模型有格式可模仿。

这里有个深层问题值得在面试讲：**旧 rewriter 收到的是程序算好的 `requested_roles`，单次调用时 LLM 必须从自己输出的槽位推导角色**。我的处理是把推导规则写进 prompt（和代码里 `_requested_dense_roles_for` 的逻辑一致），但**不信任 LLM 推对**——下游校验会兜底（见 4）。

**(3) 主流程接线**（`QueryPlanner.plan`）：

```python
candidate = self._get_llm_planner().plan_candidate(query.original)
plan = self._validator.validate(candidate, query, planner_source="llm_validated")
return self._plan_with_llm_dense_queries(
    query, plan,
    inline_specs=candidate.get("dense_query_specs"),   # ← 新增：把 inline specs 传下去
)
```

**(4) 快路径 + 回退**（`_plan_with_llm_dense_queries`，核心代码）：

```python
requested_roles = _requested_dense_roles_for(          # 程序自己算"应该有哪些角色"
    question_type=plan.question_type, ...
)

if inline_specs is not None:
    inline_validated = _validated_llm_dense_query_specs(   # ← 与旧 rewriter 输出走同一个校验函数！
        inline_specs,
        requested_roles=requested_roles,                   # LLM 推错的角色在这里被丢弃
        comparison_basis=plan.comparison_basis,
        duration_class=plan.duration_class,
    )
    if inline_validated:
        merged = _merge_dense_query_specs(                 # ← 与旧路径同一个合并函数
            llm_specs=inline_validated,
            fallback_specs=plan.dense_query_specs,         # 缺的角色用硬编码 fallback 补
            ...
        )
        if merged:
            return replace(plan, ...,
                matched_rules=[..., "dense_query:planner_single_call"])  # trace 标记

# inline 失败 → 原来的 rewriter 路径原封不动地在下面
rewriter = self._get_dense_query_rewriter()
...
```

**整个改动的安全性论证（面试核心答案）就藏在这段代码里：**

1. **校验零特权**：inline specs 走的 `_validated_llm_dense_query_specs` 和独立 rewriter 的输出是**同一个函数**——角色白名单、8-32 词长度、禁止编造数字、比较语言检查，一条不少。单次调用没有绕过任何质量关卡。
2. **角色纠错**：`requested_roles` 仍由**程序**从已校验槽位推导。LLM 多给的角色被过滤，少给的角色由 `_merge_dense_query_specs` 用硬编码 fallback specs 补齐。
3. **失败自动回退**：校验后为空 → 落回旧的两次调用路径，行为和改动前完全一样。最坏情况 = 没省到那次调用，质量无损。
4. **可观测**：走了哪条路记录在 `matched_rules`（`dense_query:planner_single_call` vs `dense_query:llm_rewriter` vs `dense_query:hardcoded_fallback:*`），生产上可以统计快路径命中率。

**(5) 测试**（`tests/test_query_planner.py` 新增两个）：

- `test_llm_mode_uses_inline_dense_specs_without_rewriter_call`：FakeLLMPlanner 返回带合法 inline specs 的 candidate，断言 **rewriter 没被调用**（`fake_rewriter.called == False`）、matched_rules 含 planner_single_call、角色齐全且自动补了 original；
- `test_llm_mode_falls_back_to_rewriter_when_inline_specs_invalid`：inline specs 全是垃圾（unknown_role + 文本过短），断言回退发生（rewriter 被调用、matched_rules 是 llm_rewriter）。

实测：冷启动 run 的 `matched_rules` 里出现了 `dense_query:planner_single_call` —— 真实 gpt-4o-mini 按新 prompt 输出的 inline specs 通过了校验，快路径在生产中真的生效。

---

# 实测结果怎么解读（面试时引用数据）

```
冷启动（无缓存命中）:  19.3s  citations=2  validation=passed  单次调用planner生效
重复问题:              6.6s
  └ timing_ms: planner 1ms | agent检索两步共63ms | evidence_pack 10ms | 检索总计92ms
```

解读：
- planner 1ms = 改动 2 的 LLM 响应缓存命中（原本 2-4 秒）；
- 检索 92ms = 改动 3 的 embedding 缓存 + 改动 9 的 HNSW 共同作用；
- 6.6s 里剩下的 ~6.5s 是 **answer LLM 生成本身**——不可缓存（证据每次可能不同）、不可省略。这就是为什么"下一步是流式输出"：总时长不变，但用户 1 秒内开始看到内容。

---

# 没做什么、为什么（面试同样会问）

| 未做 | 原因 |
| --- | --- |
| answer 流式输出 | 与"校验后才展示"的安全设计冲突：流式=未校验内容先到达用户。需要产品级方案（流式展示+末尾追加校验状态事件+失败时前端撤回标注），不是纯后端改动 |
| async 执行模型 | 全服务层同步改异步是大重构，动每个函数签名；收益主要在并发吞吐而非单请求延迟，当前单用户 demo 优先级低 |
| retrieval.py 拆分（4016 行） | 大量测试直接 import 其内部函数，拆分是高 churn 低功能收益的纯重构，不和功能改动混在一轮做 |
| LLM-as-judge 忠实度 eval | 需要先有人工标注来校准 judge；确定性断言先建立基线 |

---

# 快速自检清单（确认自己真的理解了）

逐条用一两句话回答，卡住就回到对应小节重读：

1. `get_openai_client` 为什么用 `lru_cache` 而不是全局单例？（→ 改动1）
2. LLM 响应缓存为什么存 JSON 字符串不存 dict？key 里为什么有 model 名？（→ 改动2）
3. embedding 缓存为什么只缓存 ≤512 字符的文本？"部分命中"是怎么实现的？（→ 改动3）
4. max_tokens 把 JSON 截断了会发生什么？为什么这是安全的？（→ 改动4）
5. 句级引用覆盖为什么是 warning 不是 error？valid_evidence_ids 为什么是 allowed ∩ prompt？（→ 改动5）
6. answer eval 为什么不评文本相似度？runner 为什么要做成可注入的 Protocol？（→ 改动6）
7. `[evidence_id: chunk:123]` 这个 bug 是怎么被发现的？为什么修在后端而不是前端？（→ 改动7）
8. run 持久化为什么用 JSONB 快照而不是规范化多表？审计写入失败为什么不抛 500？（→ 改动8）
9. `vector_cosine_ops` 和 `<=>` 是什么关系？exact 模式为什么要禁 index scan 又为什么要恢复？`set_config(...,true)` 的第三个参数是什么意思？（→ 改动9）
10. 单次调用合并的安全性论证是哪三层？走了哪条路怎么观测？（→ 改动10）

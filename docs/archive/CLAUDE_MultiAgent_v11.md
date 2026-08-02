# Query - Agent Runtime 架构 (v11: SSE 双流 + LLM 推理管线优化)

## 系统架构

本项目是一个 **基于 Multi-Agent 的智能知识问答系统**，由三部分组成：

| 组件 | 路径 | 技术栈 | 端口 |
|------|------|--------|------|
| 前端 | `D:\DOWNLOAD\nginx-query` | Nginx + 原生 HTML/CSS/JS | :8080 |
| Java 后端 | `D:\IntelliJ IDEA 2025.1.3\project\Query` | Spring Boot 4.0.6 + MyBatis | :8085 |
| Python AI 服务 | 本项目 (`D:\DOWNLOAD\pycharm\query`) | FastAPI + LangChain + ChromaDB + MCP | :8000 |

**请求流向：** 前端(:8080) → Nginx 反向代理 → Java 后端(:8085) → Python AI 服务(:8000)

本项目是系统的 AI 核心，采用 **Agent Runtime 架构**，通过 **AgentCapability 声明式契约** 驱动 Agent 生命周期。

---

## 从 v10 到 v11 的变更

### 核心变更

| 变更 | v10 | v11 |
|------|-----|-----|
| 请求模式 | 同步请求-响应 | **[v11: 修改]** 异步 SSE 双流：`/qa/ask` 立即返回 run_id，前端连接两个 SSE stream |
| Runtime 事件 | 无 | **[v11: 新增]** RuntimeEventBus：per-run pub-sub 事件总线，Workflow + Generation 双层事件 |
| SSE 流 | 无 | **[v11: 新增]** `/qa/runtime/{run_id}`（Workflow 层状态）+ `/qa/callback`（Java 持久化）|
| 前端渲染 | 等待完整响应 | **[v11: 修改]** EventSource 接收 token 增量流，逐字渲染 answer |
| Extractor 输出 | 完整句子 attributes + verbose evidence | **[v11: 修改]** 短语 attributes + 核心断言 evidence（50-80 字）|
| Critic 架构 | 单级 LLM Critic（87s） | **[v11: 修改]** 两级：RuleValidator（确定性 <100ms）+ Slim LLM Critic（5-15s）|
| Generator prompt | 全量展开 KO attributes + 全量 evidence | **[v11: 修改]** token budget 截断 + analysis 精简（只传 calculations + conclusions）|
| Token Metrics | 无 | **[v11: 新增]** 每个 Agent 记录 input_tokens / output_tokens / latency |

### 架构升级路线

```
v3 (Multi-Agent 应用):
  AgentContext = 业务字段集合
  set_evidence/set_sources/...
  context.evidence → 硬编码访问
  Orchestrator 知道 Agent 内部结构

v4 (Agent Runtime 框架):
  AgentContext = 数据交换协议
  set_output/get_output
  Orchestrator 只读 Registry
  新增 Agent = 1 个类 + 注册
  通用 Merge Runtime
  元数据 + 线程安全

v5 (校验分层):
  WorkflowValidator + Registry 分层校验
  layer-based inputs 前置校验
  get_layers() → validate_capabilities(plan, layers)

v6 (Workflow Control):
  Executor / Controller 角色分离
  ControlAction → Runtime 控制信号
  ControllerAgent 基类
  PolicyValidator 策略校验
  删除 Coordinator + 简单模式
  所有请求统一 Planner → TaskGraph
  ChatAgent 替代空 TaskGraph 特殊路径

v7 (Retrieval-Extraction Split):
  KnowledgeAgent → RetrievalAgent + ExtractionAgent 拆分
  RetrievalAgent: LLM 只生成 query，代码控制 search_documents → 无条件 read_all_rows
  ExtractionAgent: 纯 LLM 无工具，Map-Reduce 策略按文档分片并行提取
  TaskNode.input_mapping: 显式数据流声明
  BFS auto-injection: input_mapping 为空时自动遍历上游祖先注入
  KnowledgeObject: 结构化知识对象
  DocumentBundle + DocumentChunk: chunk 级粒度

v8 (port_bindings 数据契约):
  [v8: 删除] BFS 自动注入（同名匹配）
  [v8: 新增] port_bindings: 唯一数据通道，Agent 参数名自由命名
  [v8: 新增] required_inputs: 必连端口声明
  [v8: 新增] dedup_key_func: 去重 key 提取函数
  [v8: 新增] auto-wire: 类型兼容性自动补齐 port_bindings
  [v8: 新增] _post_process_plan: 数值问题兜底修正
  [v8: 新增] ActionRegistry 模块化（base/retry/terminate）
  [v8: 修改] ChatAgent LLM 化 + 偏好注入
  [v8: 修改] CriticAgent → ControllerAgent 子类
  [v8: 修改] Generator source_meta: list → list[dict]
  [v8: 修改] AgentMemory: +restore_session +append_turn 双写
  [v8: 修改] _plan: 加历史上下文 + 短追问补全
  [v8: 修改] DAGDataFlowValidator: 六层校验（格式/存在/上游/output_key/类型/必选）

v9 (CodeAgent 代码执行 + 图表生成):
  [v9: 新增] CodeAgent: LLM 生成 Python 代码 → 沙箱执行 → 结果返回
  [v9: 新增] CodeExecutor: 独立子进程沙箱（模块白名单/内存限制/超时）
  [v9: 新增] CodeResult: code/output/stdout/error/success/image_paths/image_data
  [v9: 新增] 图片全链路: Python PNG → base64 → Java MinIO → 前端展示
  [v9: 新增] ChartController: GET /charts/{objectName} 图片代理
  [v9: 新增] WebMvcConfig: /charts/** 免鉴权
  [v9: 新增] Nginx: /charts/ 反向代理到 Java 后端
  [v9: 新增] 前端: markdown 表格渲染 + 图片容器 + 响应式样式
  [v9: 修改] Generator: 图表已生成时不再提及文件路径
  [v9: 修改] Planner: code agent 使用规则 + port_bindings 示例
  [v9: 修改] Code prompt: 饼图防重叠规则（合并小占比为"其他"）
  [v9: 修改] Dockerfile: 添加 fonts-wqy-zenhei 中文字体
  [v9: 修改] pyproject.toml: +matplotlib, mcp<2.0

v10 (文档摘要 + 搜索过滤 + 前端会话优化):
  [v10: 新增] DocumentSummaryCache: Redis 逻辑过期 + Java API 回源的文档摘要缓存
  [v10: 新增] 文档摘要生成: 上传时 LLM 读取全文生成 2-3 句摘要
  [v10: 新增] 搜索相关性过滤: MCP Server 层 LLM 批量判断文档摘要与问题相关性
  [v10: 新增] Java Summary API: GET/PUT /document/{id}/summary（免鉴权）
  [v10: 新增] MySQL: document 表加 summary TEXT 字段
  [v10: 新增] prompts.yaml: summary.generate + relevance.judge prompt 模板
  [v10: 修改] 项目名称: RAG 知识管理系统 → 智能知识问答系统
  [v10: 修改] 前端 Logo: "RAG" → "AI"
  [v10: 修改] 前端会话管理: 延迟写库（发送首条消息后才创建）+ 防重复创建 + 切换保留对话
  [v10: 修改] Java WebMvcConfig: /document/*/summary 加入 excludePathPatterns
  [v10: 修改] Document 实体: +summary 字段
  [v10: 修改] DocumentMapper.xml: insert/update 加 summary

v11 (SSE 双流 + LLM 推理管线优化):
  [v11: 新增] RuntimeEventBus: per-run pub-sub 事件总线（Workflow Layer + Generation Layer）
  [v11: 新增] SSE 双流: /qa/runtime/{run_id}（Workflow 层）+ /qa/answer/{run_id}（Generation 层 token 增量）
  [v11: 新增] RuleValidator: 纯确定性规则检查器（空 answer + 数值一致性 normalize）
  [v11: 新增] Token Metrics: 每个 Agent 记录 input_tokens / output_tokens / latency
  [v11: 新增] Planner AgentStep: Planner 完成后写入 context.steps，前端 trace 首行显示 Planner
  [v11: 新增] plan_generated 事件: 携带 tasks 含 status 字段，前端 DAG 实时渲染
  [v11: 修改] /qa/ask: 异步模式，Python 立即返回 run_id，后台 asyncio.create_task 执行
  [v11: 修改] Java /qa/ask: 简化为只拿 run_id 立即返回，所有持久化移到 /qa/callback
  [v11: 新增] Java /qa/callback: Python 执行完后 POST 完整结果，Java 持久化到 MySQL/Redis/MinIO
  [v11: 修改] Nginx: /qa/runtime/ 和 /qa/answer/ 直接代理到 Python(:8000)，绕过 Java
  [v11: 修改] Critic 架构: 单级 LLM → 两级（RuleValidator <100ms + Slim LLM Critic 5-15s）
  [v11: 修改] Critic prompt: 删除 retrieval_report/task_plan，改为"矛盾检测"而非"支撑检测"
  [v11: 修改] Generator prompt: KnowledgeObjects token budget + analysis 只传 calculations + conclusions
  [v11: 修改] Extractor prompt: attributes 从完整句子改为短语 + 证据从冗长改为 50-80 字核心断言
  [v11: 修改] AgentContext: +run_id +event_bus +emit() 方法
  [v11: 修改] 前端: EventSource 接收 SSE 实时更新 DAG 状态 + trace 首行显示 Planner
```

---

## [v11 新增] SSE 双流架构

### 问题背景

v10 使用同步请求-响应：前端 POST /qa/ask → 阻塞等待 30-120s → 一次性返回完整 answer。用户体验差，无法感知进度。

### 设计目标

- 前端即时感知 Agent 执行进度（哪个 Agent 在运行）
- answer 逐 token 流式渲染（打字机效果）
- 无阻塞：POST 立即返回，SSE 负责推送

### 架构

```
前端 POST /qa/ask
  → Python 立即返回 {run_id, session_id}
  → 后台 asyncio.create_task(_run_and_emit)

前端同时连接两个 SSE:
  GET /qa/runtime/{run_id}   ← Workflow 层事件（低频）
  GET /qa/answer/{run_id}    ← Generation 层 token（高频）
```

### RuntimeEventBus — per-run pub-sub 事件总线

**文件**: `app/core/runtime_event_bus.py`

```python
class EventType(str, Enum):
    # Workflow Layer（低频，状态变化）
    RUNTIME_STARTED = "runtime_started"
    PLAN_GENERATED = "plan_generated"
    AGENT_STARTED = "agent_started"
    AGENT_COMPLETED = "agent_completed"
    AGENT_FAILED = "agent_failed"
    CONTROL_ACTION = "control_action"
    RUNTIME_COMPLETED = "runtime_completed"
    RUNTIME_ERROR = "runtime_error"
    # Generation Layer（高频，token 增量）
    TOKEN_CHUNK = "token_chunk"

class RuntimeEventBus:
    """per-run pub-sub 事件总线

    每个 run_id 支持多个 subscriber（runtime SSE + answer SSE 独立消费）
    每个 subscriber 有独立的 asyncio.Queue（pub-sub，非竞争消费）
    预创建 channel：/qa/ask 时先 create_channel，再启动任务，避免事件丢失
    """
    def __init__(self):
        self._channels: dict[str, list[asyncio.Queue]] = {}

    def create_channel(self, run_id: str): ...
    def subscribe(self, run_id: str) -> asyncio.Queue: ...
    async def publish(self, event: RuntimeEvent): ...
    def cleanup(self, run_id: str): ...
```

### SSE 端点

**文件**: `app/api/qa.py`

| 端点 | 说明 | 事件类型 |
|------|------|----------|
| `POST /qa/ask` | 异步启动，返回 `{run_id, session_id}` | — |
| `GET /qa/runtime/{run_id}` | Workflow 层事件流（低频） | plan_generated, agent_started, agent_completed, runtime_completed 等 |
| `GET /qa/answer/{run_id}` | Generation 层 token 增量（高频） | token_chunk, done |
| `POST /qa/callback` | Python → Java 持久化回调 | — |

**`/qa/runtime/{run_id}` 流程：**
```python
queue = event_bus.subscribe(run_id)
while True:
    event = await queue.get()
    yield f"data: {json.dumps({'type': event.type.value, 'data': event.data})}\n\n"
    if event.type in (RUNTIME_COMPLETED, RUNTIME_ERROR):
        break
event_bus.cleanup(run_id)
```

**`/qa/answer/{run_id}` 流程（token 增量）：**
```python
queue = event_bus.subscribe(run_id)
while True:
    event = await queue.get()
    if event.type == EventType.TOKEN_CHUNK:
        yield f"data: {json.dumps(event.data)}\n\n"
    elif event.type in (RUNTIME_COMPLETED, RUNTIME_ERROR):
        yield f"data: {json.dumps({'type': 'done'})}\n\n"
        break
```

### AgentContext 扩展

**文件**: `app/core/agent_context.py`

```python
@dataclass
class AgentContext:
    run_id: str = ""
    event_bus: object | None = None  # RuntimeEventBus（避免循环导入）

    async def emit(self, event_type, data: dict | None = None):
        """便捷发射 SSE 事件"""
        if self.event_bus and self.run_id:
            from app.core.runtime_event_bus import RuntimeEvent
            await self.event_bus.publish(RuntimeEvent(
                run_id=self.run_id, type=event_type, data=data or {}))
```

### Agent 发射事件的位置

| Agent | 发射点 | 事件类型 | 数据 |
|-------|--------|----------|------|
| Orchestrator | `run()` 开始 | `RUNTIME_STARTED` | session_id, question |
| Orchestrator | `_plan()` 完成 | `PLAN_GENERATED` | goal, tasks[{id, agent, objective, depends_on, status}] |
| Runtime | `_run_plan_task` 开始 | `AGENT_STARTED` | task_id, agent, objective |
| Runtime | `_run_plan_task` 完成 | `AGENT_COMPLETED` | task_id, agent, duration_ms, summary, tools_used |
| Runtime | `_run_plan_task` 失败 | `AGENT_FAILED` | task_id, agent, duration_ms, error |
| Generator | `astream` 每个 chunk | `TOKEN_CHUNK` | token 增量 |
| Orchestrator | 全部完成 | `RUNTIME_COMPLETED` | 完整 result |
| Orchestrator | 异常 | `RUNTIME_ERROR` | error |

**Planner 步骤记录：** `_plan()` 完成后，Orchestrator 将 Planner 作为 AgentStep 写入 `context.steps`（供前端 trace 展示），同时发射 `PLAN_GENERATED` 事件（含 task status 字段供前端 DAG 实时渲染）。
| Orchestrator | 全部完成 | `RUNTIME_COMPLETED` |

### Java 端 — /qa/ask 简化 + /qa/callback 持久化

**文件**: `java/.../service/QaServiceImpl.java`

```
前端 → Java POST /qa/ask → 调 Python /qa/ask → 拿到 {run_id}
  → 立即返回 {run_id} 给前端（不等待 Python 执行完成）

Python 后台执行完成 → POST /qa/callback → Java 持久化到 MySQL/Redis/MinIO
```

**关键变更：**
- `ask()`: 只调 Python 拿 run_id，不再同步等待完整结果
- `handleCallback()`: 接收 Python callback，执行全部持久化逻辑（Redis memory + Redis history + MinIO 图片 + MySQL 插入 + 会话标题）
- `sessionUserMap`: ConcurrentHashMap，ask() 写入 session→userId，handleCallback() 读取后清除

### Nginx SSE 代理

**文件**: `nginx.conf` / `nginx-docker.conf`

```
/qa/runtime/  → Python(:8000)  ← SSE 端点直接到 Python，绕过 Java
/qa/answer/   → Python(:8000)  ← SSE 端点直接到 Python
/qa/*         → Java(:8085)    ← 其他 QA 接口走 Java
```

SSE 需要关闭缓冲：`proxy_buffering off; proxy_cache off; proxy_http_version 1.1;`

### 前端 EventSource

```
POST /qa/ask → 获得 {run_id, session_id}
  → new EventSource("/qa/runtime/" + run_id)
      → plan_generated: 立即渲染 DAG（所有节点 pending）
      → agent_started:  更新 DAG 节点为 running
      → agent_completed: 更新 DAG 节点为 completed
      → agent_failed:   更新 DAG 节点为 failed
      → runtime_completed: 关闭 SSE，调 loadQaHistory() 加载完整结果
```

**前端 DAG 实时渲染流程：**
1. 收到 `plan_generated` 事件 → `_renderLiveDag(tasks)` 立即显示 DAG（所有节点灰色 pending）
2. 收到 `agent_started` 事件 → `_updateDagNodeStatus(taskId, 'running')` 节点变蓝
3. 收到 `agent_completed` 事件 → `_updateDagNodeStatus(taskId, 'completed')` 节点变绿
4. 收到 `runtime_completed` 事件 → 关闭 SSE → `loadQaHistory()` 加载完整结果（含 trace + answer + 图片）

---

## [v11 修改] LLM 推理管线优化

### 问题背景

v10 的 LLM 推理耗时瓶颈：

| Agent | 耗时 | 瓶颈原因 |
|-------|------|----------|
| Retrieval | 3s | 正常 |
| Extractor | 23.5s | 并行 ✓，但输出冗余 |
| Generator | 49s | prompt 过大 — 全部 KO attributes + evidence |
| Critic | 87.3s | prompt 极大 — 全部 evidence + answer + retrieval_report + task_plan |

### 优化策略总览

```
Phase 3（源端精简）: Extractor 输出短语化 + evidence 核心断言
    ↓ 精简后 Critic/Generator prompt 自动瘦身
Phase 1: RuleValidator (<100ms) + Slim LLM Critic (5-15s)
Phase 2: Generator token budget + analysis 精简
```

**执行顺序：先 Phase 3 验证质量 → 再 Phase 1+2。**

---

### Phase 3: Extractor 输出精简

**文件**: `app/core/prompts/prompts.yaml` — extractor.system

**改动点：**

| 项目 | v10 | v11 |
|------|-----|-----|
| attributes 值格式 | 完整句子 | 短语 + 信息密度 |
| evidence.statement | 冗长多句 | 核心断言，50-80 字 |
| 知识对象数量 | 不限 | 宁少勿滥（3-5 个关键属性）|

```yaml
# v11 修改后
extractor:
  system: |
    规则：
    - attributes 值使用短语 + 信息密度，不要用完整句子
      正确: {"purpose": ["S3C2410X中断控制", "ARM IRQ响应流程"]}
      错误: {"purpose": "掌握S3C2410X中断控制寄存器、中断响应过程以及ARM异常处理流程"}
    - 每个知识对象提取核心属性，宁少勿滥（3-5 个关键属性即可）
    - evidence.statement 只写核心断言，一句话，50-80字以内
      正确: "实验五采用中断机制，响应时间约5ms"
      错误: "实验五采用了中断机制来处理外部事件，通过配置中断控制寄存器INTMASK和INTPND..."
```

**文件**: `app/core/agents/extraction_agent.py` — FALLBACK_SYSTEM_PROMPT 同步修改

**验证标准：**
- 回答质量持平或提升 → Phase 3 保留
- 回答质量明显下降 → 回退

---

### Phase 1: RuleValidator + Slim Critic

#### RuleValidator — 纯确定性规则检查

**文件**: `app/core/agents/rule_validator.py`（v11 新增）

```python
class RuleValidator:
    """纯确定性规则检查，<100ms，无 LLM 调用

    只做字符串/数值判断，不做语义判断。
    语义层面的 citation coverage 交给 slim LLM Critic。
    """

    def check(self, answer: str, analysis: AnalysisResult | None = None) -> RuleCheckResult:
        problems = []

        # 1. 空/过短（<3 字几乎肯定是生成失败）
        if not answer or len(answer.strip()) < 3:
            problems.append("回答过短或为空")

        # 2. 数值一致性（带 normalize）
        if analysis and analysis.calculations:
            for calc in analysis.calculations:
                if calc.result is not None:
                    if not self._value_present_in_answer(calc.result, answer):
                        problems.append(f"计算结果 {calc.field}={calc.result} 未在回答中体现")

        return RuleCheckResult(passed=len(problems) == 0, problems=problems)
```

**数值 normalize（95 / 95% / 0.95 互转）：**

```python
def _value_present_in_answer(self, calc_result, answer: str) -> bool:
    # 1. 直接字符串包含（快速路径）
    result_str = str(calc_result)
    if result_str in answer:
        return True

    # 2. normalize 后比较
    norm = self._normalize_number(calc_result)
    if norm is None:
        return True  # 无法 normalize，放行

    # 提取 answer 中所有数值 token
    for match in re.finditer(r"-?\d+\.?\d*%?", answer):
        token = match.group()
        answer_norm = self._normalize_number(token)
        if answer_norm is None:
            continue
        # 整数比较（95 == 95.0）
        if norm == answer_norm:
            return True
        # 百分比互转（0.95 == 95%）
        if "%" in token and abs(norm * 100 - answer_norm) < 0.01:
            return True
        if "%" in result_str and abs(norm / 100 - answer_norm) < 0.01:
            return True

    return False
```

**不做 Source Filename 匹配**：来源引用检查（answer 是否提及 source_meta 文件名）规则太弱，语义判断交给 LLM Critic。

#### Critic 两级执行

**文件**: `app/core/agents/critic_agent.py`

```
Answer → RuleValidator (纯确定性, <100ms) → 通过?
                                          ↓ 不通过 → 直接重试（不调 LLM）
                                LLM Critic (5-15s, slim prompt)
```

```python
async def run(self, context, **kwargs):
    evidences = kwargs.get("evidence_list", [])
    analysis = kwargs.get("analysis_result")
    answer = kwargs.get("generated_answer", "")

    # ===== 1. RuleValidator（<100ms）=====
    rule_result = self.rule_validator.check(answer=answer, analysis=analysis)
    if not rule_result.passed:
        context.set_output("need_retry", True, producer="critic")
        context.set_output("retry_target", "generator", producer="critic")
        return context  # 不调 LLM，直接重试

    # ===== 2. Slim LLM Critic =====
    prompt = self._build_slim_prompt(context, evidences, analysis, answer)
    result = await self.llm.ainvoke([("human", prompt)])
    # token metrics...
```

#### Slim Critic Prompt

**文件**: `app/core/prompts/prompts.yaml` — critic.slim_evaluate

**v10 → v11 变化：**

| 项目 | v10 | v11 |
|------|-----|-----|
| prompt 模板 | `critic.evaluate` | `critic.slim_evaluate` |
| 输入 | evidence + answer + analysis + retrieval_report + task_plan | evidence + answer + analysis（无 retrieval_report/task_plan）|
| 评估标准 | claim→evidence 支撑检查 | answer↔evidence 矛盾检测 |
| evidence 处理 | 逐条展开完整内容 | 精简后全量传入（每条 50-80 字）|

```yaml
critic:
  slim_evaluate: |
    你是一个答案质量评估员。审核以下回答。

    用户问题：{question}
    回答：{answer}
    关键证据：{evidence}
    分析结果：{analysis}

    评估标准：
    - 准确性：回答是否与证据矛盾（不是检查每个claim是否有evidence支撑）
    - 完整性：是否回答了用户的问题
    - 逻辑一致性：回答是否自洽

    注意：
    - 不要因为 evidence 中缺少某个细节就判定为编造，只要回答与已有证据不矛盾且合理即可通过
    - evidence 只是文档摘要，generator 可能从原文中提取了更多细节

    返回 JSON：
    - {{"score": 8, "problems": [], "need_retry": false, "retry_target": "all"}} — 答案合格
    - {{"score": 4, "problems": ["问题描述"], "need_retry": true, "retry_target": "generator"}} — 需要修改
    - {{"score": 3, "problems": ["证据不足"], "need_retry": true, "retry_target": "retrieval"}} — 需要重新检索
```

**关键设计决策：**

- **Evidence = 约束条件**：evidence 约束回答"不能说什么"（矛盾检测），知识对象决定回答"说什么"
- **不做 citation coverage**：source filename 匹配太弱，语义覆盖判断交给 LLM
- **全量传入 evidence**：Extractor 精简后每条 50-80 字，30 条也仅 ~2400 字，不需要截断

---

### Phase 2: Generator Prompt 瘦身

**文件**: `app/core/generator/answer_generator.py`

**改动点：**

| 项目 | v10 | v11 |
|------|-----|-----|
| KnowledgeObjects | 逐个展开全部 attributes | token budget 截断（list 值 max_items=3, max_tokens=80）|
| evidence | 全量传入（不变） | 全量传入（Extractor 已精简）|
| analysis | 全量传入（含 findings） | 只传 calculations + conclusions，跳过 findings |
| 代码执行结果 | — | 已有，不变 |

#### KnowledgeObjects 紧凑格式

```python
if structured_knowledge:
    ko_lines = []
    for i, ko in enumerate(structured_knowledge[:20], 1):
        attrs = []
        for k, v in ko.attributes.items():
            if isinstance(v, list):
                val = self._truncate_list(v, max_items=3, max_tokens=80)
            else:
                val = str(v)[:80]
            attrs.append(f"{k}={val}")
        ko_lines.append(f"  {i}. [{ko.source}] {ko.topic}: {'; '.join(attrs)}")
    parts.append(f"\n知识对象（{len(structured_knowledge)}条）：\n" + "\n".join(ko_lines))
```

#### Token Budget 截断

```python
@staticmethod
def _truncate_list(items: list, max_items: int = 3, max_tokens: int = 80) -> str:
    """token budget 截断：短字段取前 N 个，长字段按 token 截断"""
    result = []
    total = 0
    for item in items:
        s = str(item)
        if len(result) >= max_items:
            break
        if total + len(s) > max_tokens and result:
            break
        result.append(s)
        total += len(s)
    text = ", ".join(result)
    if len(items) > len(result):
        text += f" 等{len(items)}项"
    return text
```

#### Analysis 精简

```python
# v10: 传入全部 analysis_result（含 findings）
# v11: 只传 calculations + conclusions
if analysis_result:
    if analysis_result.calculations:
        calc_lines = [f"  - {c.operation}({c.field})={c.result}"
                     for c in analysis_result.calculations]
        parts.append(f"\n计算结果：\n" + "\n".join(calc_lines))
    if analysis_result.conclusions:
        parts.append(f"\n结论：\n" + "\n".join(f"  - {c}" for c in analysis_result.conclusions))
```

---

### Token Metrics

每个 Agent 的 LLM 调用后记录 token 使用情况：

```python
result = await self.llm.ainvoke([("human", prompt)])
if hasattr(result, 'response_metadata') and 'token_usage' in result.response_metadata:
    usage = result.response_metadata['token_usage']
    logger.info("[AgentName] tokens: input=%d, output=%d",
                usage.get('prompt_tokens', 0), usage.get('completion_tokens', 0))
```

**记录位置：**
- `answer_generator.py`: Generator LLM 调用后
- `critic_agent.py`: Critic LLM 调用后
- Generator 同时记录 prompt 长度：`logger.info("[Generator] 生成回答完成，prompt=%d字, answer=%d字", ...)`

---

## Agent Runtime 架构设计

### 核心原则

> **Planner 决定 Workflow，Registry 提供能力，Validator 限制组合，Runtime 执行并响应 Controller。**

### 职责边界

| 角色 | 职责 | 不能做什么 |
|------|------|-----------|
| **Planner** | 选择哪些 Agent 加入 DAG，定义依赖关系和 port_bindings | 决定 Controller 执行什么 action |
| **Controller Agent** | 运行时判断是否需要修改 Workflow，输出 ControlAction | 直接修改 DAG 或跳过 Runtime |
| **Runtime** | 执行 DAG，按 port_bindings 注入数据，响应 ControlAction（重跑子树、终止等） | 绕过 Planner 自行决定 Agent 顺序 |

### 架构分层

```
                 User
                  |
            Workflow Planner
                  |
              TaskGraph
                  |
    +-------------+-------------+
    |             |             |
 WorkflowValidator  AgentRegistry  PolicyValidator
 (图结构合法性)    (Agent 能力合法性) (Controller 组合策略)
    |             |             |
    +-------------+-------------+
                  |
         GoalValidator + DAGDataFlowValidator
       (目标可达性)      (port_bindings 类型兼容)
                  |
              Agent Runtime
                  |
       +----------+----------+
       |                     |
  Executor Agent       Controller Agent
  (产生数据)            (修改运行时)
       |                     |
       +----------+----------+
              AgentContext
           outputs + traces
```

### 校验六层

```
TaskGraph
    |
    +--- 1. WorkflowValidator.validate_structure
    |      (空图、依赖存在性、循环检测、分层计算)
    |
    +--- 2. AgentRegistry.validate_capabilities
    |      (Agent 注册、outputs 冲突、control_actions 契约)
    |
    +--- 3. PolicyValidator.validate_controller_usage
    |      (Controller 位置合法性)
    |
    +--- 4. GoalValidator.validate_goal_capability
    |      (goal_outputs 在所有 Agent 中可达)
    |
    +--- 5. GoalValidator.validate_goal_reachability
    |      (当前 DAG 实际能产出 goal_outputs)
    |
    +--- 6. DAGDataFlowValidator.validate_port_bindings
           (格式/存在性/上游关系/output_key/端口存在+类型兼容/必选端口完整)
```

注意：第 1 层失败时 2-6 不执行（`if not errors` 短路）。

---

## 项目结构

```
query/
├── pyproject.toml
├── .env
├── Dockerfile
├── docker-compose.yml
├── docs/                       # 文档归档
│   ├── archive/                # 历史设计文档
│   └── plan/                   # 架构设计文档
└── app/
    ├── main.py                  # FastAPI 入口
    ├── config.py                # 配置管理
    ├── exceptions.py            # 业务异常
    ├── stream_consumer.py       # RabbitMQ 消费者
    ├── api/
    │   ├── ingestion.py         # 文档向量化 API [+v10: 上传后生成摘要]
    │   └── qa.py                # 问答 API [+v11: SSE 双流 + 异步模式]
    ├── core/
    │   ├── agent_context.py     # Agent 共享上下文 [+v11: +run_id +event_bus +emit()]
    │   ├── agent_memory.py      # 会话记忆管理
    │   ├── agent_orchestrator.py # Agent 编排器
    │   ├── agent_registry.py    # Agent 能力注册表（含 CodeAgent）
    │   ├── code_executor.py     # 沙箱化 Python 代码执行器
    │   ├── document_processor.py # 文档解析/切片
    │   ├── rag_engine.py        # RAG 核心引擎
    │   ├── runtime_event_bus.py # [v11 新增] per-run pub-sub 事件总线（Workflow + Generation 双层）
    │   ├── workflow_validator.py # DAG 校验器（六层）
    │   ├── utils.py             # 工具函数 (extract_json)
    │   ├── log_config.py        # JSON 结构化日志
    │   ├── infra/               # 基础设施层
    │   │   ├── redis_store.py   # Redis 读取封装
    │   │   ├── vector_store.py  # 向量数据库
    │   │   ├── llm_factory.py   # LLM 工厂函数
    │   │   └── summary_cache.py # [v10 新增] 文档摘要缓存
    │   ├── prompts/             # 提示词管理
    │   │   ├── prompt_manager.py # 提示词管理器
    │   │   └── prompts.yaml     # 提示词模板 [+v11: critic.slim_evaluate, extractor 精简]
    │   ├── actions/             # Action 注册与分发（模块化）
    │   │   ├── __init__.py      # ActionRegistry + create_default
    │   │   ├── base.py          # ActionHandler 抽象基类
    │   │   ├── retry.py         # RetryHandler
    │   │   └── terminate.py     # TerminateHandler
    │   ├── mcp/
    │   │   ├── client.py        # MCP Client
    │   │   ├── server.py        # MCP Server [+v10: search_documents 过滤]
    │   │   ├── session_manager.py # Session 管理
    │   │   └── tools.py         # LangChain 工具封装
    │   ├── agents/
    │   │   ├── base_agent.py    # BaseAgent + ControllerAgent 基类
    │   │   ├── chat_agent.py    # 问候/闲聊（LLM 化）
    │   │   ├── retrieval_agent.py  # 知识检索（代码控制搜索 + 全量加载）
    │   │   ├── extraction_agent.py # 知识提取（纯 LLM，Map-Reduce）[+v11: FALLBACK 同步精简]
    │   │   ├── analysis_agent.py   # 数据分析
    │   │   ├── code_agent.py       # 代码执行（LLM 生成 + 沙箱执行）
    │   │   ├── rule_validator.py    # [v11 新增] 纯确定性规则检查器
    │   │   └── critic_agent.py     # 答案审核 (ControllerAgent) [+v11: 两级设计]
    │   └── generator/
    │       └── answer_generator.py # 答案生成 [+v11: token budget + analysis 精简]
    └── models/
        ├── capability.py        # AgentCapability（含 dedup_key_func）
        ├── control.py           # ControlAction
        ├── task_graph.py        # TaskGraph（含 port_bindings）
        ├── mcp_session.py       # MCPSession
        ├── data_types.py        # Evidence, AnalysisResult, CodeResult 等
        └── schemas.py           # Pydantic 模型（含 image_urls）
```

---

## [v10 新增] 文档摘要与搜索过滤

### 问题背景

搜索时拉入了无关文档（如"账.xlsx"），导致 Extractor 处理 7 个文档（166 个知识对象），耗时长且质量差。在文档上传时生成摘要，用于搜索时判断文档相关性，可以过滤无关文档。

### 设计原则

- **Python 后端无状态**：不维护任何进程内缓存/状态，所有状态存 Redis/MySQL
- **逻辑过期**：Redis 中存储 `expire_at` 时间戳，读取时检查是否过期，过期则回源 MySQL 重新加载
- **一次成本**：生成摘要时读取文档全部内容（而非前 3 个 chunk），LLM 调用是一次性开销
- **LLM 相关性判断**：搜索时用 LLM 批量判断文档摘要是否与问题相关

### 架构

```
上传文档 → Python 读取全部内容 → LLM 生成摘要 → 调 Java API 写入 MySQL + Redis
搜索时 → Python 查 Redis(逻辑过期) → 命中且未过期直接用，过期/未命中 → Java API 查 MySQL → 回写 Redis
搜索过滤 → Python 收集候选文档摘要 → LLM 批量判断相关性 → 过滤无关文档
删除/重传文档 → Python 调 Java API 删除摘要 → 清理 Redis
```

### DocumentSummaryCache — Redis 逻辑过期 + Java API 回源

**文件**: `app/core/infra/summary_cache.py`

```python
EXPIRE_MIN = 2 * 24 * 3600   # 2天（逻辑过期下限）
EXPIRE_MAX = 3 * 24 * 3600   # 3天（逻辑过期上限，随机防雪崩）
RENEW_TTL = 7 * 24 * 3600    # Redis 物理 TTL 7天（兜底）

class DocumentSummaryCache:
    """文档摘要缓存：Redis 逻辑过期 + MySQL 回源

    Redis value: {"summary": "...", "expire_at": timestamp}
    无进程内状态，Python 后端保持无状态
    """

    async def get(self, document_id: int) -> str | None:
        """获取摘要：Redis 命中且未过期 → 直接返回；过期/未命中 → 回源 Java API (MySQL)"""
        # 1. 查 Redis → 检查 expire_at → 未过期则刷新 TTL 并返回
        # 2. 回源 Java API GET /document/{id}/summary
        # 3. 写入 Redis（逻辑过期 + 物理 TTL）

    async def set(self, document_id: int, summary: str):
        """写入摘要（Java API PUT + Redis）"""

    async def delete(self, document_id: int):
        """删除摘要（Redis 删除，MySQL 随文档删除）"""

    async def get_batch(self, document_ids: list[int]) -> dict[int, str]:
        """批量获取摘要"""
```

### 搜索过滤流程

**文件**: `app/core/mcp/server.py`

```python
# search_documents 工具中，_execute_search 之后：
async def search_documents(query, row_start, row_end):
    # 1. 执行原始搜索
    result = rag_engine.execute_search(query, row_start, row_end, state)

    # 2. 从结果中提取文档 ID
    doc_ids = {row["document_id"] for row in data["data"]}

    # 3. 批量获取摘要
    summaries = await summary_cache.get_batch(list(doc_ids))

    # 4. LLM 判断相关性
    relevant_ids = await _judge_relevance(query, summaries)

    # 5. 过滤结果（如果全部过滤掉则回退到原始结果）
    filtered = [row for row in data["data"] if row["document_id"] in relevant_ids]
    return json.dumps(filtered_data) if filtered else result
```

### Ingestion 生成摘要

**文件**: `app/api/ingestion.py`

```python
# ingest_document 成功后：
async def ingest_document(request, vector_store, summary_cache):
    # ... 向量化完成后 ...
    all_text = "\n".join([c.text for c in chunks])
    summary = await _generate_summary(all_text, document_id)
    if summary:
        await summary_cache.set(request.document_id, summary)
```

---

## [v10 修改] 前端会话管理

### 问题背景

三个前端 bug：
1. 切换页面再切回来，丢失当前对话
2. 新建任务出现在列表中间而非顶部
3. 可以连续创建多个新任务（应只允许一个）
4. 新对话立即写入数据库，可以被删除

### 解决方案：延迟写库 + 会话状态管理

引入 `state.pendingSession`：本地暂存的新会话，不调 API，不写数据库。只有用户发送消息时才真正创建。

### 会话生命周期

```
用户点击"新建任务"
  → handleNewSession() → state.pendingSession = { id: 'pending-1', ... }
  → 按钮禁用，不调 API

用户发送消息
  → handleSendQuestion() → Api.createSession() 写库
  → state.currentSessionId = real_id
  → Api.ask() → loadQaHistory()

用户切换到文档页再切回
  → switchToQa() → 检查 _awaitingFirstResponse
  → 如果为 true → 跳过 loadQaHistory()，DOM 保留用户气泡+loading

API 响应到达
  → loadQaHistory() → renderHistoricalMessages()
  → 清除 pending 状态
```

---

## AgentCapability — 数据契约

Agent 通过 `AgentCapability` 声明自己的输入、输出、工具、合并策略和去重函数，Orchestrator 只读 Registry 驱动生命周期：

```python
@dataclass
class AgentCapability:
    name: str
    description: str = ""
    inputs: dict[str, type] = field(default_factory=dict)       # 输入端口 → 类型
    required_inputs: set[str] = field(default_factory=set)      # 必连端口
    outputs: dict[str, Any] = field(default_factory=dict)       # 输出 key → 类型
    tools: list[str] = field(default_factory=list)               # 可用工具列表
    tool_descriptions: dict[str, str] = field(default_factory=dict)  # 工具中文描述
    merge_policy: dict[str, str] = field(default_factory=dict)  # 合并策略 (replace/dedup/append)
    dedup_key_func: dict[str, Callable] = field(default_factory=dict)  # output_key → 去重 key 函数
    control_actions: list[str] = field(default_factory=list)     # 控制 action
    control_outputs: list[str] = field(default_factory=list)     # 控制信号 key
    terminal: bool = False
    allow_root_controller: bool = False
```

### merge_policy 策略

| 策略 | 语义 | 适用 |
|------|------|------|
| `"replace"` | 直接替换（默认） | analysis, answer, critique, code_result |
| `"dedup"` | 去重合并（需配合 dedup_key_func） | evidence, sources |
| `"append"` | 直接追加 | document_bundle, knowledge_objects |

### 各 Agent Capability 一览

| Agent | inputs | required_inputs | outputs | tools | merge_policy |
|-------|--------|----------------|---------|-------|--------------|
| Chat | `{}` | `set()` | answer | 无 | — |
| Retrieval | `{}` | `set()` | document_bundle, retrieval_report | search_documents, read_all_rows | document_bundle=append |
| Extractor | `{"knowledge_document": DocumentBundle}` | `{"knowledge_document"}` | knowledge_objects, evidence, sources | 无 | knowledge_objects=append, evidence=dedup, sources=dedup |
| Analysis | `{}` | `set()` | analysis | calculate_sum, calculate_rank | analysis=replace |
| Code | `{"document_bundle": DocumentBundle}` | `{"document_bundle"}` | code_result | 无 | code_result=replace |
| Generator | `{"structured_knowledge": list, "evidence_list": list, "source_meta": list, "analysis_result": AnalysisResult \| None, "code_result": CodeResult \| None}` | `{"structured_knowledge", "evidence_list", "source_meta"}` | answer | 无 | answer=replace |
| Critic (ControllerAgent) | `{"evidence_list": list, "generated_answer": str, "retrieval_report": RetrievalReport, "analysis_result": AnalysisResult \| None}` | `{"evidence_list", "generated_answer", "retrieval_report"}` | critique, need_retry, retry_target | 无 | critique=replace |

---

## ControlAction — Runtime 控制信号

```python
@dataclass
class ControlAction:
    action_type: str              # "retry" / "terminate" / "pause"
    target_task_id: str | None    # retry 目标 task（可以是 agent 名）
    payload: dict
```

Controller Agent 在运行时返回 `ControlAction` 列表，Runtime 通过 `ActionRegistry` 分发处理：

```
CriticAgent.execute()
  → return [ControlAction(action_type="retry", target_task_id="retrieval")]
  → ActionRegistry.handle()
    → RetryHandler.execute()
      → plan.invalidate_subgraph({"task1"})  # 按 agent 名查找 task
      → 继续 DAG 执行循环
```

### ActionRegistry 模块结构

```
app/core/actions/
├── __init__.py      # ActionRegistry（注册表 + create_default）
├── base.py          # ActionHandler 抽象基类
├── retry.py         # RetryHandler — 按 agent 名查找 task，invalidate_subgraph
└── terminate.py     # TerminateHandler
```

新增 action type 只需：1）写一个 Handler 子类 2）在 `create_default()` 中注册。

---

## AgentContext — 数据交换协议

### 字段布局

```python
@dataclass
class AgentContext:
    # ── 系统字段（初始化后只读）──
    question: str
    session_id: str | None = None
    mcp_session_id: str = ""
    document_ids: list[int] | None = None
    history: list[dict] | None = None
    memory_context: str | None = None
    top_k: int = 5
    preferences: dict | None = None
    plan: TaskGraph | None = None
    run_id: str = ""                              # [v11 新增] SSE run_id
    event_bus: object | None = None               # [v11 新增] RuntimeEventBus

    # ── Agent 数据交换容器 ──
    outputs: dict[str, dict[str, AgentOutput]]

    # ── 执行轨迹 ──
    traces: list[AgentTrace] = field(default_factory=list)
    steps: list[AgentStep] = field(default_factory=list)
    start_time: float = field(default_factory=time.time)

    # ── 运行时上下文 ──
    current_task_id: str = ""
    merge_policies: dict[str, str] = field(default_factory=dict)
    dedup_key_funcs: dict[str, Callable] = field(default_factory=dict)

    # ── 兼容字段 ──
    tools_called: list[str] = field(default_factory=list)
    is_agg: bool = False

    # ── 线程安全 ──
    _lock: RLock = field(default_factory=RLock, repr=False, compare=False)
```

### task_id 隔离写 + 自动合并

多个 task 可以同时向同一 output key 写入而不会互相覆盖。`set_output` 按 `current_task_id` 隔离存储，`get_output` 自动合并：

```python
outputs: dict[str, dict[str, AgentOutput]]
#          ^key    ^task_id -> AgentOutput
```

**contextvars 隔离：** `asyncio.gather` 并发时 `current_task_id` 是共享字段，会被覆盖。引入 `contextvars.ContextVar` 实现 asyncio-task-local 隔离：

```python
_task_id_var: contextvars.ContextVar[str] = contextvars.ContextVar('agent_task_id', default='')

def set_output(self, key, value, producer=""):
    task_id = _task_id_var.get() or self.current_task_id
    entries[task_id] = AgentOutput(value=value, ...)
```

### 通用 API

| 方法 | 说明 |
|------|------|
| `set_output(key, value, producer)` | 按 `current_task_id` 隔离写入 |
| `get_output(key, default)` | 获取值，多 task 写入时自动 list concat |
| `get_output_entry(key, task_id)` | 获取完整 AgentOutput 元数据 |
| `clear_outputs(keys)` | 按 key 清空所有 task 的写入 |
| `has_output(key)` | 检查 key 是否存在 |
| `emit(event_type, data)` | **[v11 新增]** 发射 SSE 事件 |

---

## TaskGraph — DAG 任务图

```python
@dataclass
class TaskNode:
    id: str                      # "task1"
    agent: str                   # "retrieval" / "extractor" / "analysis" / "code" / "generator"
    objective: str               # "获取2024销售数据"
    depends_on: list[str] = field(default_factory=list)
    output_key: str = ""         # 本任务输出标识
    port_bindings: dict[str, str] = field(default_factory=dict)
    status: TaskStatus = TaskStatus.PENDING

@dataclass
class TaskGraph:
    goal: str = ""
    goal_outputs: list[str] = field(default_factory=list)
    tasks: list[TaskNode] = field(default_factory=list)

    def get_descendants(task_id) -> set[str]
    def invalidate_subgraph(task_ids) -> set[str]
```

### port_bindings — 唯一数据通道

```python
# v8+ — 仅 port_bindings
for port_name, source_ref in task.port_bindings.items():
    if "." not in source_ref:
        continue
    source_task_id, output_key = source_ref.split(".", 1)
    entry = context.get_output_entry(output_key, task_id=source_task_id)
    if entry and entry.value is not None:
        upstream_kwargs[port_name] = entry.value  # ← 端口名 = 参数名
```

---

## CodeAgent — 代码执行

### 架构

```
Planner 生成 DAG
  └── CodeAgent (task)
        ├── 1. MCP read_all_rows → 获取完整数据
        ├── 2. _build_data_summary() → 数据摘要
        ├── 3. _generate_code() → LLM 生成 Python 代码
        ├── 4. CodeExecutor.execute() → 沙箱子进程执行
        │     ├── 模块白名单: json, math, matplotlib, numpy 等
        │     ├── 内存限制: 512MB (RLIMIT_AS)
        │     ├── 超时: 60s
        │     └── stdout 捕获 + result 变量提取
        ├── 5. 图片处理: PNG → base64 编码 → 清理本地文件
        └── 6. set_output("code_result", CodeResult)
```

### CodeResult 数据结构

```python
@dataclass
class CodeResult:
    code: str = ""
    output: Any = None
    stdout: str = ""
    error: str = ""
    success: bool = True
    execution_time_ms: int = 0
    retry_count: int = 0
    image_paths: list[str] = field(default_factory=list)
    image_data: list[str] = field(default_factory=list)   # base64 PNG
```

### 图片全链路

```
CodeAgent 生成 matplotlib 代码
  → CodeExecutor 沙箱执行 → 保存 PNG 到 /app/generated/{uuid}.png
  → CodeAgent 读取 PNG → base64 编码 → 删除本地文件
  → CodeResult.image_data = ["base64..."]
  → qa.py: image_urls = code_result.image_data
  → MultiAgentResponse.image_urls = ["base64..."]
  → Java QaServiceImpl: base64 解码 → MinIO putObject("charts/{uuid}.png")
  → MySQL: qa_history.image_urls = '["charts/uuid.png"]'
  → 前端: <img src="/charts/uuid.png">
  → Nginx → ChartController → MinIO getObject → 返回 PNG
```

---

## AgentRegistry — 能力注册表

```python
class AgentRegistry:
    def register(capability, instance=None)
    def get(name)
    def get_agent(name)
    def all_capabilities()
    def valid_names()
    def find_executors()
    def find_controllers()
    def find_by_tool(tool_name)
    def find_by_writes(field_name)
    def validate_capabilities(plan, layers)
    def format_executors_for_prompt()
    def format_controllers_for_prompt()
```

### create_default_registry(llm=None)

注册 7 个 Agent（Chat, Retrieval, Extractor, Analysis, Code, Critic, Generator），内部完成 import + 实例化 + 注册。

---

## Orchestrator 核心流程

```python
async def run(self, context: AgentContext) -> AgentContext:
    # 1. 恢复记忆
    await self._restore_memory(context)

    # 2. 创建 per-request MCP session
    context.mcp_session_id = await self.mcp_client.create_session()

    # 3. 设置文档权限（per-session）
    if context.document_ids:
        await self.mcp_client.call_tool(
            "set_document_ids", {"ids": context.document_ids},
            session_id=context.mcp_session_id,
        )

    try:
        # 4. 偏好检测（后台线程，与 Planner 并行）
        pref_task = None
        if context.session_id:
            pref_task = asyncio.create_task(
                asyncio.to_thread(self.agent_memory.update_preferences, ...))

        # 5. Planner 生成 TaskGraph（与偏好检测并行）
        plan = self._plan(context.question, context.memory_context, context.history)
        if not plan or not plan.tasks:
            plan = self._fallback_plan(context.question)

        # 5a. Plan 后处理：LLM 可能不遵守规则，代码层兜底修正
        if plan and plan.tasks:
            plan = self._post_process_plan(plan, context.question, context.memory_context)

        if plan and plan.tasks:
            context.plan = plan
            # 记录 Planner 步骤到 trace（供前端展示）
            context.steps.append(AgentStep(
                name="Planner",
                duration_ms=int((time.time() - plan_start) * 1000),
                summary=f"生成 {len(plan.tasks)} 节点 DAG",
            ))
            # 发射 plan_generated 事件（含 task status，前端 DAG 实时渲染）
            await context.emit(EventType.PLAN_GENERATED, {
                "goal": plan.goal,
                "tasks": [
                    {"id": t.id, "agent": t.agent, "objective": t.objective,
                     "depends_on": t.depends_on, "status": t.status.value}
                    for t in plan.tasks
                ],
            })
            await self._execute_plan(context, plan)

        # 6. 更新记忆
        if context.session_id:
            self._update_memory(context)

    finally:
        if pref_task:
            try:
                await pref_task
            except Exception as e:
                logger.warning("偏好检测失败: %s", e)
        await self.mcp_client.cleanup_session(context.mcp_session_id)

    return context
```

### _execute_plan 详细流程

```python
async def _execute_plan(self, context, plan):
    # 收集所有 Agent 的 merge_policy 和 dedup_key_func
    context.merge_policies = {}
    context.dedup_key_funcs = {}
    for cap in self.registry.all_capabilities():
        for key, policy in cap.merge_policy.items():
            context.merge_policies[key] = policy
        for key, func in cap.dedup_key_func.items():
            context.dedup_key_funcs[key] = func

    # 自动补齐缺失的 port_bindings
    self._auto_wire_port_bindings(plan)

    for _ in range(max_iterations):  # max=10，防止 Controller 死循环
        completed_ids = {t.id for t in plan.tasks if t.status == TaskStatus.COMPLETED}
        pending = [t for t in plan.tasks if t.status in (PENDING, RETRYING)]

        if not pending:
            break

        while pending:
            ready = [t for t in pending if all(d in completed_ids for d in t.depends_on)]
            if not ready:
                break

            # 并行执行 ready 任务 — task_id 隔离写，不冲突
            await asyncio.gather(*(self._run_plan_task(context, task, ...) for task in ready))

            for task in ready:
                completed_ids.add(task.id)
                pending.remove(task)

        # 全部完成（没有 Controller 触发 retry），退出
        remaining = [t for t in plan.tasks if t.status in (PENDING, RETRYING)]
        if not remaining:
            break

    # 校验 goal_outputs
    if plan and plan.goal_outputs:
        missing = [o for o in plan.goal_outputs if not context.has_output(o)]
        if missing:
            raise WorkflowExecutionError(f"DAG 未产生目标输出: {missing}")
```

---

## Plan 后处理 (_post_process_plan)

LLM 可能不遵守 Planner prompt 规则，代码层兜底修正：

### 规则 1：数值问题强制 analysis

检测到求和/排名关键词但 plan 中无 analysis → 自动插入 analysis task，连接到 retrieval 下游，并加入 generator 的 depends_on。

### 规则 2：纯数值移除 extractor

求和/排名问题不需要 extractor 的文本提取（Extractor 耗时 90-160s）→ 移除所有 extractor task。

### 规则 3：清理失效 port_bindings

移除 extractor 后，清理所有引用已移除 task ID 的 depends_on 和 port_bindings。

---

## 信息流

所有请求统一走 Planner → TaskGraph：

```
Question → Planner → TaskGraph → DAG Runtime → Answer

DAG Runtime 内部:
  _execute_plan()
    ├── auto_wire_port_bindings（类型兼容自动补齐）
    ├── 拓扑排序 → ready 任务
    ├── 并行执行: asyncio.gather(ready tasks)
    │     └── 无依赖的 task 同时执行，task_id 隔离写冲突
    ├── port_bindings → kwargs 注入下游 Agent
    ├── Controller: 返回 ControlAction
    │     └── retry → invalidate_subgraph(task_ids) → 下一轮循环
    └── goal_outputs 校验
```

### [v11 修改] SSE 信息流

```
用户发送问题
  → POST /qa/ask (Java) → 调 Python /qa/ask → 返回 {run_id}
  → 前端拿到 run_id

前端连接 SSE:
  GET /qa/runtime/{run_id} (Nginx → Python)
    ← plan_generated: DAG 出现，所有 task pending
    ← agent_started: task 变为 running（蓝色）
    ← agent_completed: task 变为 completed（绿色）
    ← runtime_completed: 关闭 SSE，加载完整结果

Python 后台执行完:
  → _callback_java() → Java /qa/callback → 持久化 MySQL + Redis + MinIO
```

---

## 记忆管理

### AgentMemory

进程内记忆管理器，纯内存无持久化。Java MySQL 是 source of truth。

```python
class AgentMemory:
    REWRITE_INTERVAL = 10  # 每 10 轮触发 LLM 重写里程碑
    FACT_HARD_LIMIT = 50   # facts 列表上限
    FACT_PRUNE_TRIGGER = 40  # 触发压缩的阈值
    FACT_KEEP_RECENT = 15   # 压缩时保留最近 N 条不动
```

### Orchestrator 记忆操作

```python
# _restore_memory: 请求开始时恢复
async def _restore_memory(self, context):
    # 1. 从 Redis 恢复 memory 快照
    if not self.agent_memory.has_session(context.session_id):
        loaded = await self.redis_store.safe_get_memory(context.session_id)
        if loaded:
            self.agent_memory.restore_session(context.session_id, loaded)

    # 2. recent_history 缓存满 → 直接用；不满 → 从 Redis 补齐
    # 3. 无 memory 且有 history → rebuild（幂等）

# _update_memory: 请求结束时更新
def _update_memory(self, context):
    tool_desc_map = {}
    for cap in self.registry.all_capabilities():
        tool_desc_map.update(getattr(cap, 'tool_descriptions', {}))
    mapped_tools = [tool_desc_map.get(t, t) for t in context.tools_called]

    self.agent_memory.update(context.session_id, {...})
    self.agent_memory.append_turn(context.session_id, question, answer)
```

---

## MCP 架构

### 架构概览

MCP Server 常驻运行，作为 Tool Runtime 服务所有请求。每个请求通过 session_id 隔离状态：

```
FastAPI Request
       |
AgentContext { mcp_session_id: "uuid" }
       |
AgentOrchestrator
       |
MCPClient (singleton, 不持有 session_id)
       |
  create_session() → uuid
  cleanup_session(uuid)
       |
MCP Server (常驻 subprocess)
       |
  +-- session_A { search_ctx, document_ids }
  +-- session_B { search_ctx, document_ids }
       |
RAGEngine (无状态)
       |
  +-- DocumentSummaryCache [v10]
  +-- RedisStore
```

### MCP Server — 工具清单

| 工具 | 参数 | 说明 |
|------|------|------|
| `set_document_ids` | session_id, ids | 设置文档权限 |
| `search_documents` | session_id, query, row_start?, row_end? | 搜索文档 **[v10: +LLM 相关性过滤]** |
| `list_documents` | session_id | 列出可检索文档 |
| `calculate_sum` | session_id, key_name, row_filter?, content_filter? | 求和 |
| `calculate_rank` | session_id, key_name, ascending, position?, content_filter? | 排名 |
| `read_all_rows` | session_id | 读取完整数据（**CodeAgent 主要依赖此工具**） |

---

## 模块职责

### core/ — 核心业务层

**基础设施:**
- `llm_factory.py` — `create_llm(temperature, max_tokens, timeout)` 统一创建 ChatOpenAI
- `utils.py` — `extract_json(text)` 从 LLM 输出提取 JSON
- `log_config.py` — JSON 结构化日志
- `summary_cache.py` — **[v10]** 文档摘要缓存：Redis 逻辑过期 + Java API 回源
- **`runtime_event_bus.py`** — **[v11 新增]** per-run pub-sub 事件总线（Workflow + Generation 双层）

**业务模块:**
- `document_processor.py` — 文档解析/切片（1000字符/200重叠），支持 PDF/DOCX/DOC/TXT/MD/XLSX
- `vector_store.py` — Chroma + Ollama Embeddings 封装
- `rag_engine.py` — RAG 核心引擎：搜索(60→0.92→关键词→多样性)、算法计算(不调 LLM)
- `code_executor.py` — 沙箱化 Python 代码执行器（模块白名单/内存限制/超时）

**Agent Runtime:**
- `agent_orchestrator.py` — Planner → TaskGraph → auto-wire → port_bindings 注入 → 执行 → 记忆更新
- `agent_registry.py` — capability 注册 + 实例化 + prompt 生成 + 能力校验（含 CodeAgent）
- `workflow_validator.py` — WorkflowValidator + PolicyValidator + GoalValidator + DAGDataFlowValidator 六层校验
- `agent_context.py` — outputs 容器 + 线程安全 + 按 task_id 隔离 + contextvars **[+v11: run_id + event_bus + emit()]**
- `agent_memory.py` — 会话记忆：事实提取、里程碑、偏好检测、LRU 淘汰、restore_session、append_turn

**Agent 实现:**
- `base_agent.py` — BaseAgent + ControllerAgent 基类
- `chat_agent.py` — 问候/闲聊，LLM 化 + 偏好注入
- `retrieval_agent.py` — LLM 生成 query，代码控制 search_documents → read_all_rows → DocumentBundle
- `extraction_agent.py` — 纯 LLM，Map-Reduce 按文档分片并行提取 KnowledgeObject **[+v11: FALLBACK 同步精简]**
- `analysis_agent.py` — 数值计算 + Tool Calling
- `code_agent.py` — LLM 生成 Python 代码 + 沙箱执行 + 图片 base64 编码
- **`rule_validator.py`** — **[v11 新增]** 纯确定性规则检查器（空 answer + 数值一致性 normalize）
- `critic_agent.py` — 继承 ControllerAgent **[v11: 两级设计 — RuleValidator + Slim LLM Critic]**
- `answer_generator.py` — LLM 答案生成 **[v11: token budget + analysis 精简 + token metrics]**

### models/ — 数据模型

- `capability.py` — AgentCapability（inputs/required_inputs/outputs/merge_policy/dedup_key_func）
- `control.py` — ControlAction 数据类
- `task_graph.py` — TaskNode + TaskGraph（port_bindings + get_descendants + invalidate_subgraph）
- `mcp_session.py` — MCPSession（session_id, document_ids, search_ctx）
- `data_types.py` — Evidence, AnalysisResult, Calculation, CriticResult, AgentResult, DocumentBundle, DocumentChunk, KnowledgeObject, RetrievalReport, AgentTrace, CodeResult
- `schemas.py` — Pydantic 请求/响应模型（MultiAgentResponse 含 image_urls）

---

## 外部依赖服务

| 服务 | 地址 | 用途 |
|------|------|------|
| Ollama | localhost:11434 | Embedding (nomic-embed-text) |
| DeepSeek API | api.deepseek.com | LLM (deepseek-chat) |
| ChromaDB | 本地 ./chroma_db/ | 向量数据库 |
| Redis | localhost:6379 | 记忆和历史持久化 + **文档摘要缓存** |
| RabbitMQ | localhost:5672 | 文档向量化任务队列 |
| MinIO | localhost:9000 | 文档文件存储 + 图表图片存储 |

---

## 新增 Agent 的步骤

1. 编写 Agent 类，继承 `BaseAgent`（或 `ControllerAgent` 如果是 Controller）
2. 定义 `capability`（inputs/required_inputs/outputs/merge_policy/dedup_key_func/control_actions 等）
3. 在 `agent_registry.py` 的 `create_default_registry()` 中 import + 实例化 + 注册
4. 在 `prompts.yaml` 的 `planner.system` 中添加新 Agent 的能力描述和使用规则

**如果新 Agent 使用现有数据类型：** 不需要修改其他 Agent 的 inputs/outputs，auto-wire 自动连接。

**如果新 Agent 引入新数据类型：** 下游消费者的 `inputs` 需要加端口。

---

## 开发指南

### 启动服务

```bash
.venv\Scripts\activate
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

### Docker

```bash
docker compose up -d          # 全部
docker compose up python-ai   # 仅 Python
docker compose logs -f python-ai
```

---

## 修改注意事项

| 修改项 | 文件位置 | 说明 |
|--------|----------|------|
| 文档切片参数 | `core/document_processor.py` | chunk_size, chunk_overlap |
| 搜索阈值 | `core/rag_engine.py` | SCORE_THRESHOLD (0.92) |
| Embedding/LLM 模型 | `.env` | EMBEDDING_*, LLM_* |
| API 路径 | `app/api/` | 需同步更新 Java 后端 |
| Agent 分类逻辑 | `core/agents/retrieval_agent.py` | QUERY_PROMPT |
| 搜索/计算限制 | `core/rag_engine.py` | search_count>2, agg_count>8 |
| 提示词模板 | `core/prompts/prompts.yaml` | knowledge/retrieval/extractor/analysis/code/generator/critic/planner **+summary+relevance [+v11: slim_evaluate]** |
| LLM 创建参数 | `core/infra/llm_factory.py` | create_llm(temperature, max_tokens, timeout) |
| Agent 能力声明 | `models/capability.py` | AgentCapability (inputs/outputs/merge_policy/dedup_key_func) |
| 合并策略 | `models/capability.py` | merge_policy (dedup/replace/append) |
| 去重键规则 | `models/capability.py` | dedup_key_func per output key |
| 新增 Agent | `core/agent_registry.py` | create_default_registry() 中加 import + 注册 |
| 新增 Action | `core/actions/` | 写 Handler 子类 + create_default() 中注册 |
| Planner 规则 | `core/prompts/prompts.yaml` | planner.system |
| Plan 后处理 | `core/agent_orchestrator.py` | _post_process_plan() |
| Auto-wire | `core/agent_orchestrator.py` | _auto_wire_port_bindings() |
| DAG 结构校验 | `core/workflow_validator.py` | WorkflowValidator.validate_structure() |
| 能力校验 | `core/agent_registry.py` | validate_capabilities(plan, layers) |
| Controller 策略 | `core/workflow_validator.py` | PolicyValidator.validate_controller_usage() |
| port_bindings 校验 | `core/workflow_validator.py` | DAGDataFlowValidator.validate_port_bindings() |
| 记忆淘汰策略 | `core/agent_memory.py` | idle_ttl, max_sessions, REWRITE_INTERVAL |
| MCP session TTL | `core/mcp/session_manager.py` | SessionManager._TTL (默认 1800s) |
| 代码执行沙箱 | `core/code_executor.py` | TIMEOUT, MAX_MEMORY_MB, _SAFE_MODULES |
| Code prompt | `core/prompts/prompts.yaml` | code.system 段 |
| 图表生成规则 | `core/prompts/prompts.yaml` | code.system 图表防重叠规则 |
| 图片上传 [Java] | `QaServiceImpl.java` | base64 解码 + MinIO putObject |
| 图片代理 [Java] | `ChartController.java` | GET /charts/{objectName} |
| 图片展示 [前端] | `app.js` + `style.css` | renderQaMessages() 图片渲染 |
| **文档摘要缓存** [v10] | `core/infra/summary_cache.py` | Redis 逻辑过期 + Java API 回源 |
| **摘要生成** [v10] | `api/ingestion.py` | 上传后调 LLM 生成摘要 |
| **搜索过滤** [v10] | `core/mcp/server.py` | search_documents 后 LLM 相关性过滤 |
| **Summary API** [v10 Java] | `DocumentController.java` | GET/PUT /document/{id}/summary |
| **Summary 免鉴权** [v10 Java] | `WebMvcConfig.java` | excludePathPatterns 加 /document/*/summary |
| **Summary 字段** [v10 Java] | `Document.java` + `DocumentMapper.xml` | summary TEXT |
| **前端会话管理** [v10] | `app.js` | 延迟写库 + 防重复创建 + 切换保留对话 |
| **SSE 双流** [v11] | `api/qa.py` | /qa/ask 异步 + /qa/runtime SSE + /qa/callback |
| **RuntimeEventBus** [v11] | `core/runtime_event_bus.py` | per-run pub-sub 事件总线 |
| **AgentContext 扩展** [v11] | `core/agent_context.py` | +run_id +event_bus +emit() |
| **RuleValidator** [v11] | `core/agents/rule_validator.py` | 纯确定性规则检查器 |
| **Critic 两级** [v11] | `core/agents/critic_agent.py` | RuleValidator + Slim LLM |
| **Generator 瘦身** [v11] | `core/generator/answer_generator.py` | token budget + analysis 精简 |
| **Extractor 精简** [v11] | `core/prompts/prompts.yaml` | attributes 短语化 + evidence 核心断言 |
| **Critic slim prompt** [v11] | `core/prompts/prompts.yaml` | critic.slim_evaluate |
| **Token Metrics** [v11] | Generator + Critic | input_tokens/output_tokens/latency |
| **Java SSE 代理** [v11 Java] | `QaController.java` | SseEmitter 代理 + /qa/callback |
| **前端 EventSource** [v11 前端] | `app.js` | 逐字渲染 answer |

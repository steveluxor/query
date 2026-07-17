# Query - Agent Runtime 架构 (v6: Capability-driven Workflow Control)

## 系统架构

本项目是一个 **Multi-Agent RAG 智能知识库问答系统**，由三部分组成：

| 组件 | 路径 | 技术栈 | 端口 |
|------|------|--------|------|
| 前端 | `D:\DOWNLOAD\nginx-query` | Nginx + 原生 HTML/CSS/JS | :8080 |
| Java 后端 | `D:\IntelliJ IDEA 2025.1.3\project\Query` | Spring Boot 4.0.6 + MyBatis | :8085 |
| Python AI 服务 | 本项目 (`D:\DOWNLOAD\pycharm\query`) | FastAPI + LangChain + ChromaDB + MCP | :8000 |

**请求流向：** 前端(:8080) → Nginx 反向代理 → Java 后端(:8085) → Python AI 服务(:8000)

本项目是系统的 AI 核心，采用 **Agent Runtime 架构**，通过 **AgentCapability 声明式契约** 驱动 Agent 生命周期。

---

## Agent Runtime 架构设计

### 核心原则

> **Planner 决定 Workflow，Registry 提供能力，Validator 限制组合，Runtime 执行并响应 Controller。**

### 职责边界

| 角色 | 职责 | 不能做什么 |
|------|------|-----------|
| **Planner** | 选择哪些 Agent 加入 DAG，定义依赖关系 | 决定 Controller 执行什么 action |
| **Controller Agent** | 运行时判断是否需要修改 Workflow，输出 ControlAction | 直接修改 DAG 或跳过 Runtime |
| **Runtime** | 执行 DAG，响应 ControlAction（重跑子树、终止等） | 绕过 Planner 自行决定 Agent 顺序 |

### 架构分层

```
                 User
                  |
            Workflow Planner
                  |
              TaskGraph
                  |
        +---------+---------+
        |                   |
 WorkflowValidator   CapabilityValidator
 (图结构合法性)      (Agent 能力合法性)
        |                   |
        +---------+---------+
                  |
          PolicyValidator
       (Controller 组合策略)
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

### 校验三层

```
TaskGraph
    |
    +----------+----------+
    |                     |
    v                     v
WorkflowValidator    AgentRegistry
(DAG 结构)           (Agent 契约)
    |                     |
    +----------+----------+
               |
               v
      PolicyValidator
   (Controller 组合策略)
               |
               v
         Orchestrator
```

| 层 | 组件 | 职责 | 校验项 |
|----|------|------|--------|
| DAG 结构 | `WorkflowValidator` | 图结构合法性（与 Agent 无关） | 空图、依赖存在性、循环检测、分层计算 |
| 能力契约 | `Registry.validate_capabilities` | Agent 能力匹配 | Agent 注册、inputs 前置、output 冲突、control_actions 契约 |
| 组合策略 | `PolicyValidator` | Agent 组合语义合法性 | Controller 位置、control_output 不被 Executor 消费 |

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
  TaskGraph.subgraph_invalidation 支持通用 retry
  create_default_registry() 统一实例化
  Orchestrator 无 Agent 直接 import
```

---

## 项目结构

```
query/
├── pyproject.toml
├── .env
├── Dockerfile
├── docker-compose.yml
└── app/
    ├── main.py                  # FastAPI 入口
    ├── config.py                # 配置管理
    ├── exceptions.py            # 业务异常
    ├── stream_consumer.py       # RabbitMQ 消费者
    ├── api/
    │   ├── ingestion.py         # 文档向量化 API
    │   └── qa.py                # 问答 API
    ├── core/
    │   ├── llm_factory.py       # LLM 工厂函数
    │   ├── utils.py             # 工具函数 (extract_json)
    │   ├── log_config.py        # JSON 结构化日志
    │   ├── document_processor.py # 文档解析/切片
    │   ├── vector_store.py      # 向量数据库
    │   ├── rag_engine.py        # RAG 核心引擎
    │   ├── agent_context.py     # Agent 共享上下文
    │   ├── agent_orchestrator.py # Agent 编排器 (v6: role 分派)
    │   ├── agent_registry.py    # Agent 能力注册表 (v6: 统一实例化)
    │   ├── workflow_validator.py # DAG 校验器 + PolicyValidator (v6)
    │   ├── agent_memory.py      # 会话记忆管理
    │   ├── redis_store.py       # Redis 读取封装
    │   ├── prompt_manager.py    # 提示词管理器
    │   ├── prompts.yaml         # 提示词模板
    │   ├── mcp/
    │   │   ├── client.py        # MCP Client
    │   │   ├── server.py        # MCP Server
    │   │   ├── session_manager.py # Session 管理
    │   │   └── tools.py         # LangChain 工具封装
    │   ├── agents/
    │   │   ├── base_agent.py    # Agent 基类 + ControllerAgent (v6)
    │   │   ├── chat_agent.py    # 问候/闲聊 (v6: 新增)
    │   │   ├── knowledge_agent.py  # 知识检索
    │   │   ├── analysis_agent.py   # 数据分析
    │   │   └── critic_agent.py     # 答案审核 (v6: Controller)
    │   └── generator/
    │       └── answer_generator.py # 答案生成
    └── models/
        ├── capability.py        # AgentCapability (v6: role/control_actions)
        ├── control.py           # ControlAction (v6: 新增)
        ├── task_graph.py        # TaskGraph (v6: subgraph invalidation)
        ├── mcp_session.py       # MCPSession
        ├── data_types.py        # Evidence, AnalysisResult, CriticResult
        └── schemas.py           # Pydantic 模型
```

---

## AgentCapability — 数据契约 (v6)

Agent 通过 `AgentCapability` 声明自己的输入、输出、运行时角色和合并策略，Orchestrator 只读 Registry 驱动生命周期：

```python
class AgentRole(Enum):
    EXECUTOR = "executor"         # 普通数据节点：处理输入、产生输出
    CONTROLLER = "controller"     # 控制节点：可改变 Workflow 行为

@dataclass
class AgentCapability:
    name: str
    description: str
    inputs: list[str]
    outputs: dict[str, type]
    tools: list[str]
    merge_policy: dict[str, str]

    # v6 运行时角色与控制
    role: AgentRole = AgentRole.EXECUTOR
    control_actions: list[str]          # Runtime 可执行的控制行为（如 retry）
    control_outputs: list[str]          # Runtime 控制信号 key，不可被 Executor 消费
    terminal: bool                      # 可终止 Workflow（如 SafetyAgent）
    allow_root_controller: bool         # 允许作为 DAG 根节点（如 RouterAgent）
```

### merge_policy 策略

| 策略 | 语义 | 适用 |
|------|------|------|
| `"replace"` | 直接替换（默认） | analysis, answer, critique |
| `"dedup"` | 去重合并 | evidence, sources |
| `"append"` | 直接追加 | logs, trace |

### 各 Agent Capability 一览 (v6)

| Agent | 角色 | inputs | outputs | control_actions | merge_policy |
|-------|------|--------|---------|----------------|-------------|
| Chat | EXECUTOR | [] | answer | — | — |
| Knowledge | EXECUTOR | [] | evidence, sources, **retrieval_report** | — | evidence=dedup, sources=dedup |
| Analysis | EXECUTOR | [evidence] | analysis | — | analysis=replace |
| Generator | EXECUTOR | [evidence, analysis] | answer | — | answer=replace |
| Critic | **CONTROLLER** | [answer, **retrieval_report**] | critique, need_retry, retry_target | ["retry"] | critique=replace |

---

## ControlAction — Runtime 控制信号 (v6: 新增)

```python
@dataclass
class ControlAction:
    action_type: str              # "retry" / "terminate" / "pause"
    target_task_id: str | None    # retry 目标 task
    payload: dict
```

Controller Agent 在运行时返回 `ControlAction` 列表，Runtime 统一处理。Runtime 不关心是哪个 Controller 产生的 action：

```
CriticAgent.execute()
  → return [ControlAction(action_type="retry", target_task_id="knowledge_task")]
  → Runtime._handle_control_action()
    → plan.invalidate_subgraph({"knowledge_task"})
    → 继续 DAG 执行循环
```

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

    # ── Agent 数据交换容器 ──
    outputs: dict[str, dict[str, AgentOutput]]

    # ── 执行轨迹 ──
    traces: list[AgentTrace] = field(default_factory=list)
    steps: list[AgentStep] = field(default_factory=list)
    start_time: float = field(default_factory=time.time)

    # ── 运行时上下文 ──
    current_task_id: str = ""

    # ── 兼容字段 ──
    tools_called: list[str] = field(default_factory=list)
    is_agg: bool = False

    # ── 线程安全 ──
    _lock: RLock = field(default_factory=RLock, repr=False, compare=False)
```

所有业务数据通过 `get_output()` 访问，无直接字段（如 `context.evidence` 等）。

### task_id 隔离写 + 自动合并 (v6 关键设计)

多个 task 可以同时向同一 output key 写入而不会互相覆盖。`set_output` 按 `current_task_id` 隔离存储，`get_output` 自动合并：

```python
outputs: dict[str, dict[str, AgentOutput]]
#          ^key    ^task_id -> AgentOutput
```

**contextvars 隔离（v6.1）：** `asyncio.gather` 并发时 `current_task_id` 是共享字段，会被覆盖。引入 `contextvars.ContextVar` 实现 asyncio-task-local 隔离：

```python
_task_id_var: contextvars.ContextVar[str] = contextvars.ContextVar('agent_task_id', default='')

def set_output(self, key, value, producer=""):
    task_id = _task_id_var.get() or self.current_task_id
    entries[task_id] = AgentOutput(value=value, ...)
```

每个 asyncio Task 有独立的 Context 副本，`_task_id_var.set(task.id)` 只影响当前 Task，`asyncio.gather` 下各 task 互不干扰。

**set_output 写入规则：** `outputs[key][current_task_id] = AgentOutput(value=...)`

**get_output 合并规则：**
- 单一 task 写入 → 直接返回该值
- 多 task 写入 list 类型 → concat 合并所有列表
- 多 task 写入非 list 类型 → last writer wins

示例（并行 knowledge task）：
```
task1: set_output("evidence", [A])  →  outputs["evidence"]["task1"] = [A]
task2: set_output("evidence", [B])  →  outputs["evidence"]["task2"] = [B]
get_output("evidence")              →  [A, B]  (concat)
```

### 通用 API

| 方法 | 说明 |
|------|------|
| `set_output(key, value, producer)` | 按 `current_task_id` 隔离写入 |
| `get_output(key, default)` | 获取值，多 task 写入时自动 list concat |
| `get_output_entry(key, task_id)` | 获取完整 AgentOutput 元数据 |
| `clear_outputs(keys)` | 按 key 清空所有 task 的写入 |
| `has_output(key)` | 检查 key 是否存在 |
| `has_all_outputs(keys)` | 批量检查 |

---

## AgentRegistry — 能力注册表 (v6)

```python
class AgentRegistry:
    def register(capability, instance=None)           # 注册 Capability + 实例
    def get(name)                                     # 获取 Capability
    def get_agent(name)                               # 获取 Agent 实例
    def all_capabilities()                            # 所有 Capability
    def valid_names()                                 # 所有 Agent 名称
    def find_by_role(role)                            # 按角色查找 (v6: 新增)
    def find_executors()                              # 所有 Executor (v6: 新增)
    def find_controllers()                            # 所有 Controller (v6: 新增)
    def find_by_tool(tool_name)                       # 拥有指定工具的 Agent
    def find_by_writes(field_name)                    # 写入指定 output 的 Agent
    def validate_capabilities(plan, layers)           # Agent 能力校验
    def format_executors_for_prompt()                 # Planner prompt: Executor 列表 (v6)
    def format_controllers_for_prompt()               # Planner prompt: Controller 列表 (v6)
```

### create_default_registry(rag_engine=None) (v6: 统一实例化)

注册所有 5 个 Agent（Chat, Knowledge, Analysis, Critic, Generator），内部完成 import + 实例化 + 注册。

Orchestrator 不再 import 任何 Agent 类。

### Agent 能力校验

`validate_capabilities(plan, layers)` 校验四项：

| 校验项 | 说明 | 错误示例 |
|--------|------|----------|
| Agent 注册检查 | task.agent 是否在 Registry 中 | `Agent 'unknown' 未注册` |
| inputs 校验 | 该 Agent 的 inputs 是否在上游产出 | `analysis 缺少输入 'evidence'` |
| 输出冲突检测 | 同层多 task 写同一 output_key | `输出冲突: [t1, t2] 都写入 'evidence'` |
| control_actions 契约 | Planner 不应指定 control action | `Planner 不应指定 control action` |

---

## TaskGraph — DAG 任务图 (v6: subgraph invalidation)

```python
@dataclass
class TaskNode:
    id: str
    agent: str
    objective: str
    depends_on: list[str]
    output_key: str = ""
    status: str = "pending"    # pending / running / completed / failed / skipped

@dataclass
class TaskGraph:
    goal: str
    tasks: list[TaskNode]

    def get_descendants(task_id) -> set[str]       # v6: BFS 找所有下游
    def invalidate_subgraph(task_ids) -> set[str]  # v6: 标记子树为 pending
```

### subgraph invalidation 示例

```
Knowledge (task1) ──→ Analysis (task2) ──→ Generator (task3) ──→ Critic (task4)
                                                    │
                                                    └── Summary (task5, 独立分支)

Critic 返回 retry_target="task1" (knowledge):
  invalidate_subgraph({"task1"})
  → 重置 task1, task2, task3, task4 为 pending
  → task5 不受影响（独立分支）
```

---

## Agent 执行 — role 分派 (v6)

```python
# Orchestrator._run_plan_task()
cap = registry.get(task.agent)
if cap.role == AgentRole.CONTROLLER:
    await _execute_controller_task(context, task)
else:
    await _execute_executor_task(context, task, original_question, cap)
```

### Executor 执行流程

依赖 task_id 隔离写，无需手动清空或合并：

```
_execute_executor_task()
  ├── 设置 current_task_id = task.id
  ├── 校验前置条件 (has_all_outputs)
  ├── agent.execute() → 内部 set_output(key, value)
  │     └── 按 current_task_id 隔离写入 outputs[key][task_id]
  └── task.status = "completed"
      └── get_output() 自动合并多 task 写入（list concat）
```

多个 Executor 可并行执行（`asyncio.gather`），写入同一 output key 不会冲突。

### Controller 执行流程

```
_execute_controller_task()
  ├── 设置 current_task_id = task.id
  ├── 校验前置条件
  ├── agent.execute() → 返回 list[ControlAction]
  ├── task.status = "completed"
  └── for action in actions:
        _handle_control_action(context, action)
          ├── action_type == "retry" → plan.invalidate_subgraph() （无需清 outputs，set_output 同 task_id 覆盖旧值）
          └── action_type == "terminate" → 终止执行
```

---

## 信息流 (v6)

所有请求统一走 Planner → TaskGraph，不再有简单模式/规划模式之分：

```
Question → Planner → TaskGraph → DAG Runtime → Answer

DAG Runtime 内部:
  _execute_plan()
    ├── 拓扑排序 → ready 任务
    ├── 并行执行: asyncio.gather(ready tasks)
    │     └── 无依赖的 task 同时执行，task_id 隔离写冲突
    ├── Executor: current_task_id → agent.execute() → set_output(key, value, producer)
    ├── Controller: 返回 ControlAction
    │     └── retry → invalidate_subgraph(task_ids) → 下一轮循环
    └── 全部 completed → Generator 兜底（current_task_id = "_fallback"）
```

### 典型 DAG 示例

问候/闲聊：
```
Chat
```

单步查询：
```
Knowledge → Generator
```

查询 + 计算：
```
Knowledge → Analysis → Generator
```

查询 + 计算 + 审核：
```
Knowledge → Analysis → Generator → Critic
```

跨文档对比（并行）：
```
task1: Knowledge(搜A) ─┐
                         ├→ task3: Analysis(对比) → Generator
task2: Knowledge(搜B) ─┘
```

跨文档对比 + 审核：
```
task1: Knowledge(搜A) ─┐
                         ├→ task3: Analysis(对比) → Generator → Critic
task2: Knowledge(搜B) ─┘
```

### 证据完整性报告 (v6.1)

Knowledge Agent 输出 `retrieval_report` 追踪证据完整性，Critic Agent 据此校验证据覆盖度：

```
Knowledge Agent:
  └── _extract_retrieval_report()
        ├── 优先从 LLM JSON 输出的 retrieval_report 字段提取
        └── 兜底：从工具调用轨迹推断（search 次数、read_all_rows 调用、返回行数）
        └── 存入 context.set_output("retrieval_report", report)

Critic Agent:
  └── _build_prompt() 读取 retrieval_report
        ├── is_complete=false → need_retry=true, retry_target="knowledge"
        └── report 不存在 → 降级为当前行为（不阻止）
```

RetrievalReport 字段：

| 字段 | 说明 |
|------|------|
| sources | 搜索到的文档名列表 |
| total_chunks | 命中文档的全量 chunk 数 |
| returned_chunks | 实际返回的 chunk 数 |
| is_complete | 数据是否完整（read_all_rows 已调用 = true） |
| read_all_rows_called | 是否调用了 read_all_rows |
| searches_performed | 搜索次数 |

Prompt 层面配合：

- **Knowledge prompt**：规则中强化"数据完整性检查"，LLM 根据 `is_complete` 信号自行决定是否调 `read_all_rows`
- **Critic prompt**：评估标准增加"证据覆盖度"，`is_complete=false` 时指引 Critic 要求重新检索

---

## 通用 Merge Runtime (兼容保留)

Orchestrator 保留 `_merge_outputs()` 和 `_dedup_key()` 静态方法，但**热路径已不再使用**。v6 的 evidence 累积由 `get_output()` 的 list concat 自动完成。

```python
@staticmethod
def _merge_outputs(old_value, new_value, policy: str, output_key: str):
    if policy == "replace":    return new_value
    elif policy == "append":   return old_value + new_value (仅 list)
    elif policy == "dedup":    return deduplicated list
    return new_value           # 未知策略 fallback
```

`_dedup_key()` 为不同 output key 定制去重逻辑：

| key | 去重维度 |
|-----|---------|
| evidence | (source, statement[:200]) |
| sources | (file_name, str(item)[:200]) |
| 其他 | repr(item)[:200] |

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
SessionManager
       |
  +-- session_A { search_ctx, document_ids }
  +-- session_B { search_ctx, document_ids }
       |
RAGEngine (无状态)
```

### 核心约束

- **一个请求 = 一个 MCP session**，所有 Agent（含 Controller retry）共享同一个 session
- **session_id 只存在于 AgentContext**，MCPClient 不持有
- **Agent 不感知 session**，tool 签名中无 session_id（由 `tools.py` 自动注入）

### MCP Server — 工具清单

| 工具 | 参数 | 说明 |
|------|------|------|
| `_create_session` | session_id | 创建 session (内部) |
| `_cleanup_session` | session_id | 清理 session (内部) |
| `set_document_ids` | session_id, ids | 设置文档权限 |
| `search_documents` | session_id, query, row_start?, row_end? | 搜索文档 |
| `list_documents` | session_id | 列出可检索文档 |
| `calculate_sum` | session_id, key_name, row_filter?, content_filter? | 求和 |
| `calculate_rank` | session_id, key_name, ascending, position?, content_filter? | 排名 |
| `read_all_rows` | session_id | 读取完整数据（所有文档类型） |

---

## Agent 清单 (v6)

| Agent | 文件 | 角色 | 职责 | LLM 调用方式 |
|-------|------|------|------|-------------|
| Chat | `agents/chat_agent.py` | EXECUTOR | 问候/闲聊 | 无 LLM（静态回复） |
| Knowledge | `agents/knowledge_agent.py` | EXECUTOR | 知识检索 + 证据提取 | Tool Calling（搜索最多 2 次） |
| Analysis | `agents/analysis_agent.py` | EXECUTOR | 数值计算（求和/排名） | Tool Calling（计算最多 8 次） |
| Generator | `generator/answer_generator.py` | EXECUTOR | 答案生成 | 单次 LLM 调用 |
| Critic | `agents/critic_agent.py` | **CONTROLLER** | 答案质量审核，返回 ControlAction | 单次 LLM 调用 |

---

## Orchestrator 核心流程 (v6)

```python
async def run(self, context: AgentContext) -> AgentContext:
    # 1. 恢复记忆
    await self._restore_memory(context)

    # 2. 创建 MCP session
    context.mcp_session_id = await self.mcp_client.create_session()

    try:
        # 3. 偏好检测
        if context.session_id:
            self.agent_memory.update_preferences(...)

        # 4. Planner 生成 TaskGraph
        plan = self._plan(context.question, context.memory_context)
        if plan and plan.tasks:
            context.plan = plan
            await self._execute_plan(context, plan)

        # 5. 更新记忆
        if context.session_id:
            self._update_memory(context)

    finally:
        await self.mcp_client.cleanup_session(context.mcp_session_id)

    return context
```

### _execute_plan 详细流程

```python
async def _execute_plan(self, context, plan):
    for _ in range(max_iterations):       # 最多 10 轮（防死循环）
        pending = [t for t in plan.tasks if t.status == "pending"]
        if not pending:
            break

        while pending:
            ready = [t for t in pending if all(d in completed_ids for d in t.depends_on)]
            # 并行执行 ready 任务 — task_id 隔离写，不冲突
            await asyncio.gather(*(self._run_plan_task(context, task, ...) for task in ready))
            for task in ready:
                completed_ids.add(task.id)
                pending.remove(task)

        # 无 pending 任务 → 退出；有 pending（Controller retry 导致）→ 继续循环

    # Generator 兜底
    if not context.has_output("answer"):
        context.current_task_id = "_fallback"
        await generator.execute(context)
```

---

## 新增 Agent 的步骤 (v6)

1. 编写 Agent 类，继承 `BaseAgent`（或 `ControllerAgent` 如果是 Controller）
2. 定义 `capability`（role, inputs, outputs, merge_policy, control_actions 等）
3. 在 `agent_registry.py` 的 `create_default_registry()` 中实例化并注册

无需修改 `AgentContext`、`Orchestrator`、`Validator` 或 `Prompts.yaml`（Planner 通过 Registry 自动发现）。

---

## 模块职责

### core/ — 核心业务层

**基础设施:**
- `llm_factory.py` — `create_llm(temperature, max_tokens, timeout)` 统一创建 ChatOpenAI
- `utils.py` — `extract_json(text)` 从 LLM 输出提取 JSON
- `log_config.py` — JSON 结构化日志

**业务模块:**
- `document_processor.py` — 文档解析/切片（1000字符/200重叠），支持 PDF/DOCX/DOC/TXT/MD/XLSX
- `vector_store.py` — Chroma + Ollama Embeddings 封装
- `rag_engine.py` — RAG 核心引擎：搜索(60→0.92→关键词→多样性)、算法计算(不调 LLM)

**Agent Runtime:**
- `agent_orchestrator.py` — (v6) Planner → TaskGraph → role 分派 Executor/Controller，无 Coordinator/简单模式
- `agent_registry.py` — (v6) capability 注册 + 实例化 + prompt 生成 + 能力校验
- `workflow_validator.py` — (v6) WorkflowValidator + PolicyValidator 三层校验
- `agent_context.py` — outputs 容器 + 线程安全 + 按 task_id 隔离
- `agent_memory.py` — 会话记忆：事实提取、里程碑、偏好检测、LRU 淘汰

**Agent 实现:**
- `base_agent.py` — (v6) BaseAgent + ControllerAgent 基类
- `chat_agent.py` — (v6) 问候/闲聊，静态回复
- `knowledge_agent.py` — 知识检索 + Tool Calling
- `analysis_agent.py` — 数值计算 + Tool Calling
- `critic_agent.py` — (v6) 继承 ControllerAgent，返回 ControlAction
- `answer_generator.py` — LLM 答案生成

**MCP 协议层:**
- `client.py` — MCP Client（stdio + session 管理）
- `server.py` — MCP Server（常驻 + SessionManager）
- `session_manager.py` — Session 生命周期（创建/查询/删除/过期淘汰）
- `tools.py` — LangChain 工具封装（自动注入 session_id）

### models/ — 数据模型

- `capability.py` — AgentRole 枚举 + AgentCapability（inputs/outputs/merge_policy/v6 控制字段）
- `control.py` — (v6) ControlAction 数据类
- `task_graph.py` — TaskNode + TaskGraph（v6: get_descendants/invalidate_subgraph）
- `mcp_session.py` — MCPSession（session_id, document_ids, search_ctx）
- `data_types.py` — AgentOutput, Evidence, AnalysisResult, CriticResult, RetrievalReport, AgentTrace
- `schemas.py` — Pydantic 请求/响应模型

---

## 外部依赖服务

| 服务 | 地址 | 用途 |
|------|------|------|
| Ollama | localhost:11434 | Embedding (nomic-embed-text) |
| DeepSeek API | api.deepseek.com | LLM (deepseek-chat) |
| ChromaDB | 本地 ./chroma_db/ | 向量数据库 |
| Redis | localhost:6379 | 记忆和历史持久化 |
| RabbitMQ | localhost:5672 | 文档向量化任务队列 |
| MinIO | localhost:9000 | 文档文件存储 |

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
| 搜索/计算限制 | `core/rag_engine.py` | search_count>2, agg_count>8 |
| 提示词模板 | `core/prompts.yaml` | planner 使用 {available_executors}/{available_controllers} |
| LLM 创建参数 | `core/llm_factory.py` | create_llm(temperature, max_tokens, timeout) |
| Agent 能力声明 | `models/capability.py` | AgentCapability (role/inputs/outputs/merge_policy) |
| 合并策略 | `models/capability.py` | merge_policy (dedup/replace/append) |
| 新增 Agent | `core/agent_registry.py` | create_default_registry() 中加一行 |
| DAG 结构校验 | `core/workflow_validator.py` | validate_structure() + get_layers() |
| Agent 能力校验 | `core/agent_registry.py` | validate_capabilities(plan, layers) |
| Controller 策略 | `core/workflow_validator.py` | PolicyValidator.validate_controller_usage() |
| 去重键规则 | `core/agent_orchestrator.py` | _dedup_key() per output key（兼容保留，热路径未使用） |
| 记忆淘汰策略 | `core/agent_memory.py` | idle_ttl, max_sessions, REWRITE_INTERVAL |
| MCP session TTL | `core/mcp/session_manager.py` | SessionManager._TTL (默认 1800s) |

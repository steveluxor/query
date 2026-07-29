# Query - Agent Runtime 架构 (v7: Retrieval-Extraction Split & Code-Controlled Completeness)

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

[v7: 新增] v7 (Retrieval-Extraction Split & Code-Controlled Completeness):
  [v7: 修改] KnowledgeAgent → RetrievalAgent + ExtractionAgent 拆分
  [v7: 新增] RetrievalAgent: LLM 只生成 search query，代码控制 search_documents → 无条件 read_all_rows
  [v7: 新增] ExtractionAgent: 纯 LLM 无工具，Map-Reduce 策略按文档分片并行提取
  [v7: 新增] TaskNode.input_mapping: 显式数据流声明，下游精确消费上游 output
  [v7: 新增] BFS auto-injection: input_mapping 为空时自动遍历上游祖先注入
  [v7: 新增] KnowledgeObject: 结构化知识对象（topic + attributes），Generator 优先使用
  [v7: 新增] DocumentBundle + DocumentChunk: chunk 级粒度，支持 Map-Reduce
  [v7: 修改] 数据完整性由代码控制（无条件 read_all_rows），不再依赖 LLM 判断
  [v7: 删除] KnowledgeAgent（包括 knowledge prompt 中的 read_all_rows 逻辑）
  [v7: 删除] 证据完整性报告（v6.1 的 is_complete 信号），由代码直接保证

[v7.1: 新增] v7.1 (Classification Fix & Data Flow Completeness):
  [v7.1: 修复] RetrievalAgent QUERY_PROMPT: 增加具体例子和兜底规则，LLM 不再把"对比"误判为 aggregation
  [v7.1: 修复] Planner 示例: 对比类任务改为顺序 DAG，避免并行 retrieval 数据丢失
  [v7.1: 新增] Extractor prompt: 长度约束（完整句子、3-5属性、覆盖开头中间结尾）
  [v7.1: 新增] AgentCapability.inputs: 数据契约补充，DAGDataFlowValidator 校验 inputs 覆盖完整
  [v7.1: 修复] BFS 注入: 多上游同名 key 按 merge_policy 合并而非覆盖
  [v7.1: 修复] Generator prompt: knowledge_objects + evidence 同时传入，不再互斥
  [v7.1: 新增] AgentContext._merge_values: DocumentBundle 类型支持（chunks 合并去重）
```

---

## 项目结构 [v7: 修改]

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
    │   ├── agent_orchestrator.py # Agent 编排器 [v7: input_mapping + BFS 注入]
    │   ├── agent_registry.py    # Agent 能力注册表 [v7: 6 Agent]
    │   ├── workflow_validator.py # DAG 校验器 + PolicyValidator
    │   ├── agent_memory.py      # 会话记忆管理
    │   ├── redis_store.py       # Redis 读取封装
    │   ├── prompt_manager.py    # 提示词管理器
    │   ├── prompts.yaml         # 提示词模板 [v7: 修改]
    │   ├── actions/             # [v7: 新增] Action 注册与分发
    │   ├── mcp/
    │   │   ├── client.py        # MCP Client
    │   │   ├── server.py        # MCP Server
    │   │   ├── session_manager.py # Session 管理
    │   │   └── tools.py         # LangChain 工具封装
    │   ├── agents/
    │   │   ├── base_agent.py    # Agent 基类 + ControllerAgent
    │   │   ├── chat_agent.py    # 问候/闲聊
    │   │   ├── retrieval_agent.py  # [v7: 新增] 知识检索（代码控制搜索 + 全量加载）
    │   │   ├── extraction_agent.py # [v7: 新增] 知识提取（纯 LLM，Map-Reduce）
    │   │   ├── analysis_agent.py   # 数据分析
    │   │   └── critic_agent.py     # 答案审核 (Controller)
    │   └── generator/
    │       └── answer_generator.py # 答案生成 [v7: knowledge_objects 优先]
    └── models/
        ├── capability.py        # AgentCapability
        ├── control.py           # ControlAction
        ├── task_graph.py        # TaskGraph [v7: input_mapping 字段]
        ├── mcp_session.py       # MCPSession
        ├── data_types.py        # [v7: 修改] +DocumentChunk, DocumentBundle, KnowledgeObject
        └── schemas.py           # Pydantic 模型
```

---

## AgentCapability — 数据契约

Agent 通过 `AgentCapability` 声明自己的输入、输出、运行时角色和合并策略，Orchestrator 只读 Registry 驱动生命周期：

```python
class AgentRole(Enum):
    EXECUTOR = "executor"         # 普通数据节点：处理输入、产生输出
    CONTROLLER = "controller"     # 控制节点：可改变 Workflow 行为

@dataclass
class AgentCapability:
    name: str
    description: str
    inputs: dict[str, type] = field(default_factory=dict)  # [v7.1: 新增]
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

### 各 Agent Capability 一览 [v7.1: 修改]

| Agent | 角色 | inputs | outputs | control_actions | merge_policy |
|-------|------|--------|---------|----------------|-------------|
| Chat | EXECUTOR | — | answer | — | — |
| [v7] **Retrieval** | EXECUTOR | — | document_bundle, retrieval_report | — | document_bundle=append |
| [v7.1: 修改] **Extractor** | EXECUTOR | **document_bundle** | knowledge_objects, evidence, sources | — | knowledge_objects=append, evidence=dedup, sources=dedup |
| Analysis | EXECUTOR | — | analysis | — | analysis=replace |
| **Generator** | EXECUTOR | — | answer | — | answer=replace |
| Critic | **CONTROLLER** | — | critique, need_retry, retry_target | ["retry"] | critique=replace |

[v7.1: 新增] AgentCapability 恢复 `inputs` 字段。Extractor 声明 `inputs={"document_bundle": DocumentBundle}`，DAGDataFlowValidator 据此校验所有必需输入在 `input_mapping` 或 BFS 注入中覆盖完整。

---

## ControlAction — Runtime 控制信号

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
  → return [ControlAction(action_type="retry", target_task_id="task1")]
  → Runtime._handle_control_action()
    → plan.invalidate_subgraph({"task1"})
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
    merge_policies: dict[str, str] = field(default_factory=dict)  # [v7: 新增]

    # ── 兼容字段 ──
    tools_called: list[str] = field(default_factory=list)
    is_agg: bool = False

    # ── 线程安全 ──
    _lock: RLock = field(default_factory=RLock, repr=False, compare=False)
```

所有业务数据通过 `get_output()` 访问，无直接字段（如 `context.evidence` 等）。

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

每个 asyncio Task 有独立的 Context 副本，`_task_id_var.set(task.id)` 只影响当前 Task，`asyncio.gather` 下各 task 互不干扰。

**set_output 写入规则：** `outputs[key][current_task_id] = AgentOutput(value=...)`

**get_output 合并规则：**
- 单一 task 写入 → 直接返回该值
- 多 task 写入 list 类型 → concat 合并所有列表
- 多 task 写入 DocumentBundle 类型 → 合并所有 chunks（去重） [v7.1: 新增]
- 多 task 写入非 list 非 DocumentBundle → last writer wins

示例（并行 retrieval task）：
```
task1: set_output("document_bundle", bundle1)  →  outputs["document_bundle"]["task1"] = bundle1
task2: set_output("document_bundle", bundle2)  →  outputs["document_bundle"]["task2"] = bundle2
BFS 注入下游时按 append 合并                           →  bundle1.chunks + bundle2.chunks（去重）
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

## AgentRegistry — 能力注册表

```python
class AgentRegistry:
    def register(capability, instance=None)           # 注册 Capability + 实例
    def get(name)                                     # 获取 Capability
    def get_agent(name)                               # 获取 Agent 实例
    def all_capabilities()                            # 所有 Capability
    def valid_names()                                 # 所有 Agent 名称
    def find_by_role(role)                            # 按角色查找
    def find_executors()                              # 所有 Executor
    def find_controllers()                            # 所有 Controller
    def find_by_tool(tool_name)                       # 拥有指定工具的 Agent
    def find_by_writes(field_name)                    # 写入指定 output 的 Agent
    def validate_capabilities(plan, layers)           # Agent 能力校验
    def format_executors_for_prompt()                 # Planner prompt: Executor 列表
    def format_controllers_for_prompt()               # Planner prompt: Controller 列表
```

### create_default_registry(rag_engine=None) [v7: 修改]

[v7: 修改] 注册 6 个 Agent（Chat, **Retrieval**, **Extractor**, Analysis, Critic, Generator），内部完成 import + 实例化 + 注册。

[v7: 删除] KnowledgeAgent 不再注册。

### Agent 能力校验

`validate_capabilities(plan, layers)` 校验四项：

| 校验项 | 说明 | 错误示例 |
|--------|------|----------|
| Agent 注册检查 | task.agent 是否在 Registry 中 | `Agent 'unknown' 未注册` |
| 输出冲突检测 | 同层多 task 写同一 output_key | `输出冲突: [t1, t2] 都写入 'evidence'` |
| control_actions 契约 | Planner 不应指定 control action | `Planner 不应指定 control action` |

[v7: 修改] 移除了 inputs 校验（Agent 不再声明 inputs，数据流由 input_mapping 控制）。

---

## TaskGraph — DAG 任务图 [v7: 修改]

```python
@dataclass
class TaskNode:
    id: str
    agent: str
    objective: str
    depends_on: list[str]
    output_key: str = ""
    input_mapping: dict[str, str] = field(default_factory=dict)  # [v7: 新增]
    status: str = "pending"    # pending / running / completed / failed / skipped

@dataclass
class TaskGraph:
    goal: str
    goal_outputs: list[str] = field(default_factory=list)        # [v7: 新增]
    tasks: list[TaskNode]

    def get_descendants(task_id) -> set[str]
    def invalidate_subgraph(task_ids) -> set[str]
```

### input_mapping [v7: 新增]

`input_mapping` 定义了本 task 从哪些上游 task 获取数据。key 是 Agent `run()` 方法的参数名，value 是 `"source_task_id.output_key"` 格式的全限定引用。

```
task2 (extractor) 需要上游 task1 (retrieval) 的 document_bundle：
  TaskNode(id="task2", agent="extractor", depends_on=["task1"],
           input_mapping={"document_bundle": "task1.document_bundle"})
```

**BFS 自动注入（回退策略）：** 当 `input_mapping` 为空时，Orchestrator 自动 BFS 遍历全部上游祖先，将所有 output_key 作为 kwargs 注入。**多上游产出同名 key 时按 merge_policy 合并而非覆盖** [v7.1: 修复]：

```python
if task.input_mapping:
    # 显式 mapping：精确取指定 task+key
    for param_name, source_ref in task.input_mapping.items():
        source_task_id, output_key = source_ref.split(".", 1)
        entry = context.get_output_entry(output_key, task_id=source_task_id)
        upstream_kwargs[param_name] = entry.value
else:
    # BFS 遍历全部上游祖先注入
    visited = set()
    queue = list(task.depends_on)
    while queue:
        dep_id = queue.pop(0)
        # 遍历上游 agent 的所有 output_keys 注入
        for output_key in dep_cap.output_keys:
            entry = context.get_output_entry(output_key, task_id=dep_id)
            if output_key in upstream_kwargs:
                # [v7.1: 合并而非覆盖] 同名 key → 按 merge_policy 合并
                policy = context.merge_policies.get(output_key, "replace")
                upstream_kwargs[output_key] = AgentContext._merge_values(
                    [upstream_kwargs[output_key], entry.value], policy, output_key)
            else:
                upstream_kwargs[output_key] = entry.value
```

这意味着：
- 如果参数名与上游 output_key 一致，可以不写 input_mapping（BFS 自动匹配）
- 如果参数名不同（如 extractor 的 `document_bundle` 参数与 retrieval 的 `document_bundle` output key 同名），可以不写 input_mapping

### subgraph invalidation 示例 [v7: 修改]

```
Retrieval (task1) → Extractor (task2) → Generator (task3) → Critic (task4)
                                               │
                                               └── Summary (task5, 独立分支)

Critic 返回 retry_target="task1":
  invalidate_subgraph({"task1"})
  → 重置 task1, task2, task3, task4 为 pending
  → task5 不受影响（独立分支）
```

---

## Agent 执行

### Executor 执行流程 [v7: 修改]

依赖 task_id 隔离写，input_mapping 注入上游输出：

```
_execute_executor_task()
  ├── 解析 input_mapping 或 BFS 遍历注入 upstream_kwargs
  ├── 设置 current_task_id = task.id
  ├── agent.execute(**upstream_kwargs) → 内部 set_output(key, value)
  │     └── 按 current_task_id 隔离写入 outputs[key][task_id]
  └── task.status = "completed"
      └── get_output() 自动合并多 task 写入（list concat）
```

多个 Executor 可并行执行（`asyncio.gather`），写入同一 output key 不会冲突。

### Controller 执行流程

```
_execute_controller_task()
  ├── 设置 current_task_id = task.id
  ├── agent.execute() → 返回 list[ControlAction]
  ├── task.status = "completed"
  └── for action in actions:
        _handle_control_action(context, action)
          ├── action_type == "retry" → plan.invalidate_subgraph()
          └── action_type == "terminate" → 终止执行
```

---

## 信息流 [v7: 修改]

所有请求统一走 Planner → TaskGraph：

```
Question → Planner → TaskGraph → DAG Runtime → Answer

DAG Runtime 内部:
  _execute_plan()
    ├── 拓扑排序 → ready 任务
    ├── 并行执行: asyncio.gather(ready tasks)
    │     └── 无依赖的 task 同时执行，task_id 隔离写冲突
    ├── input_mapping → kwargs 注入下游 Agent
    │     └── BFS auto-injection 回退（input_mapping 为空时）
    ├── Controller: 返回 ControlAction
    │     └── retry → invalidate_subgraph(task_ids) → 下一轮循环
    └── 全部 completed → Generator 兜底（current_task_id = "_fallback"）
```

### 典型 DAG 示例 [v7: 修改]

问候/闲聊：
```
Chat
```

单步查询：
```
Retrieval → Extractor → Generator
```

查询 + 计算：
```
Retrieval → Extractor → Analysis → Generator
```

查询 + 计算 + 审核：
```
Retrieval → Extractor → Analysis → Generator → Critic
```

跨文档对比（顺序 DAG）[v7.1: 修改]：
```
Retrieval(搜全部) → Extractor(提取全部) → Generator(对比)
```

[v7.1: 修改] 并行 DAG 仅用于无数据依赖的独立分支（如查看文档数量 + 求和），对比/综合类任务必须使用顺序 DAG 确保数据完整性。

### 数据完整性保障 [v7: 修改] [v7: 新增]

[v7: 删除] v6.1 的「证据完整性报告」已被移除。原方案让 LLM 检查 `is_complete` 信号并决定是否调用 `read_all_rows`，但 LLM 不可靠。

[v7: 新增] **代码控制的数据完整性：** RetrievalAgent 在 search_documents 之后 **无条件**调用 `read_all_rows`，无需 LLM 参与决策。流程如下：

```
RetrievalAgent.run()
  ├── 1. LLM 生成 search query + 查询类型（temperature=0）
  │     └── 类型: "aggregation"（求和/排名/行号）→ strict 策略（单文档）
  │          "comparison"（对比/查询/总结）→ standard 策略（多文档）
  │     └── [v7.1: 兜底] 不确认时一律选 comparison，只有明确数值加总才选 aggregation
  ├── 2. 代码调 search_documents(query, strategy)
  ├── 3. 代码无条件调 read_all_rows()  [v7: 代码控制，无 LLM 决策]
  └── 4. 解析为 DocumentBundle → set_output("document_bundle", bundle)

RetrievalReport:
  is_complete 始终为 true（read_all_rows 已被代码无条件调用）
  retrievals_report 仅为审计日志，不再影响 Critic 决策
```

### Map-Reduce 提取 [v7: 新增]

Extractor 采用 Map-Reduce 策略防止多文档时 LLM 遗漏部分文档：

```
DocumentBundle (152 chunks from 6 docs)
  │
  ├─ Map: 按 source 分组，每组独立并行调 LLM
  │   ├─ 实验二 (40 chunks) → LLM → [KO₁, KO₂, ...]
  │   ├─ 实验七 (30 chunks) → LLM → [KO₃, ...]
  │   ├─ 实验三 (25 chunks) → LLM → [...]
  │   └─ ... (asyncio.gather 并行执行)
  │
  └─ Reduce: 合并所有结果
       └─ knowledge_objects = KO₁ + KO₂ + KO₃ + ... (append)
       └─ evidence = ev₁ + ev₂ + ev₃ + ...           (dedup)
```

```
_extract_single_source(source, chunks, question)
  ├── _format_single_doc() → 只包含该文档的 chunks
  ├── LLM提取（prompt 大小大幅缩小：152→20-40 chunks per doc）
  └── _parse_output() → 返回 (list[KnowledgeObject], list[Evidence])
```

### 新数据类型 [v7: 新增]

```python
@dataclass
class DocumentChunk:
    source: str          # 文件名
    content: str         # chunk 文本
    chunk_index: int = 0
    total_chunks: int = 0

@dataclass
class DocumentBundle:
    """保持 chunk 级粒度，支持 Map-Reduce 按 source 分片"""
    chunks: list[DocumentChunk] = field(default_factory=list)

@dataclass
class KnowledgeObject:
    """知识对象 — 带 topic 的结构化语义信息"""
    topic: str              # 主题/实体名（如"实验一"）
    attributes: dict = field(default_factory=dict)  # {"销售额": 100, "增长": "30%"}
    source: str = ""         # 来源文档
    confidence: float = 1.0  # 提取置信度
```

### Generator 数据源 [v7.1: 修改]

Generator 的 prompt 同时包含 `knowledge_objects`（结构化摘要）和 `evidence`（原始细节），两者互补而非互斥：

```python
def _build_prompt(self, context):
    # 知识对象（结构化摘要，始终展示）
    if knowledge_objects:
        prompt += format_knowledge_objects(knowledge_objects)
    # 证据（原始细节，与知识对象互补）
    if evidence_list:
        prompt += format_evidence(evidence_list)
    else:
        prompt += "证据：无"
```

[v7.1: 修改] v7 中 knowledge_objects 和 evidence 是互斥的（有 KO 就不看 evidence），导致"账.xlsx"的行数据虽在 evidence 中却未被 Generator 使用。v7.1 改为同时传入，LLM 同时获得结构化摘要和原始行数据。

---

## 通用 Merge Runtime (兼容保留)

Orchestrator 保留 `_merge_outputs()` 和 `_dedup_key()` 静态方法，但**热路径已不再使用**。v7 的 evidence 累积由 `get_output()` 的 list concat 自动完成。

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

## Agent 清单 [v7: 修改]

| Agent | 文件 | 角色 | 职责 | LLM 调用方式 |
|-------|------|------|------|-------------|
| Chat | `agents/chat_agent.py` | EXECUTOR | 问候/闲聊 | 无 LLM（静态回复） |
| [v7: 新增] **Retrieval** | `agents/retrieval_agent.py` | EXECUTOR | LLM 生成 query，代码控制搜索 + 无条件 read_all_rows → DocumentBundle | 单次 LLM 调用生成 query |
| [v7: 新增] **Extractor** | `agents/extraction_agent.py` | EXECUTOR | Map-Reduce：按文档分片并行 LLM 提取 KnowledgeObject | 每文档 1 次 LLM 调用（并行） |
| [v7: 删除] ~~Knowledge~~ | ~~`agents/knowledge_agent.py`~~ | ~~EXECUTOR~~ | ~~知识检索 + Evidence 提取~~ | ~~Tool Calling~~ |
| Analysis | `agents/analysis_agent.py` | EXECUTOR | 数值计算（求和/排名） | Tool Calling（计算最多 8 次） |
| [v7: 修改] **Generator** | `generator/answer_generator.py` | EXECUTOR | 答案生成，知识对象 + 证据同时使用 | 单次 LLM 调用 |
| Critic | `agents/critic_agent.py` | **CONTROLLER** | 答案质量审核，返回 ControlAction | 单次 LLM 调用 |

---

## Orchestrator 核心流程 [v7: 修改]

```python
async def run(self, context: AgentContext) -> AgentContext:
    # 1. 恢复记忆
    await self._restore_memory(context)

    # 2. 创建 MCP session
    context.mcp_session_id = await self.mcp_client.create_session()

    # 3. [v7: 新增] 设置文档权限（per-session，set_document_ids）
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
        plan = self._plan(context.question, context.memory_context)
        if plan and plan.tasks:
            context.plan = plan
            await self._execute_plan(context, plan)

        # 6. 更新记忆
        if context.session_id:
            self._update_memory(context)

    finally:
        if pref_task:
            await pref_task
        await self.mcp_client.cleanup_session(context.mcp_session_id)

    return context
```

### _execute_plan 详细流程 [v7: 修改]

```python
async def _execute_plan(self, context, plan):
    # [v7: 新增] 收集所有 Agent 的 merge_policy
    context.merge_policies = {}
    for cap in self.registry.all_capabilities():
        for key, policy in cap.merge_policy.items():
            context.merge_policies[key] = policy

    for _ in range(max_iterations):
        pending = [t for t in plan.tasks if t.status in ("pending", "retrying")]

        while pending:
            ready = [t for t in pending if all(d in completed_ids for d in t.depends_on)]
            # 并行执行 ready 任务 — task_id 隔离写，不冲突
            await asyncio.gather(*(self._run_plan_task(context, task, ...) for task in ready))
            for task in ready:
                completed_ids.add(task.id)
                pending.remove(task)

    # Generator 兜底
    if not context.has_output("answer"):
        context.current_task_id = "_fallback"
        await generator.execute(context)
```

### _run_plan_task — input_mapping 注入 [v7: 新增]

```python
async def _run_plan_task(self, context, task, original_question):
    # [v7: 新增] 按 input_mapping 从指定上游 task 获取数据
    upstream_kwargs = {}
    if task.input_mapping:
        for param_name, source_ref in task.input_mapping.items():
            source_task_id, output_key = source_ref.split(".", 1)
            entry = context.get_output_entry(output_key, task_id=source_task_id)
            if entry and entry.value is not None:
                upstream_kwargs[param_name] = entry.value
    else:
        # [v7: 新增] BFS 自动注入（input_mapping 为空时）
        visited = set()
        queue = list(task.depends_on)
        while queue:
            dep_id = queue.pop(0)
            # 遍历上游 agent 的所有 output_keys 注入
            for output_key in dep_cap.output_keys:
                entry = context.get_output_entry(output_key, task_id=dep_id)
                if entry and entry.value is not None:
                    upstream_kwargs[output_key] = entry.value
            ...

    context.current_task_id = task.id
    _task_id_var.set(task.id)
    context.question = task.objective

    result = await agent.execute(
        context, task_id=task.id,
        mcp_client=self.mcp_client, mcp_session_id=context.mcp_session_id,
        **upstream_kwargs,    # [v7: 新增] 注入上游数据
    )
```

### _fallback_plan [v7: 修改]

```python
# v6 (knowledge → generator):
TaskNode(id="task1", agent="knowledge", objective=question),
TaskNode(id="task2", agent="generator", objective=question, depends_on=["task1"]),

# v7 (retrieval → extractor → generator):
TaskNode(id="task1", agent="retrieval", objective=question),
TaskNode(id="task2", agent="extractor", objective=question, depends_on=["task1"]),
TaskNode(id="task3", agent="generator", objective=question, depends_on=["task2"]),
```

---

## prompts.yaml 变更 [v7: 修改]

### [v7: 删除] knowledge: 整段删除
- 原本包含 system prompt（search_documents + read_all_rows 工具描述、evidence 提取规则）
- 原本包含 retrieval_report 生成逻辑（is_complete 判断）
- 原本包含 evidence_type 取值说明

### [v7: 新增] retrieval: 搜索词生成
- 简短 system prompt：只生成关键词，不要解释/标点/引号
- 纯文本输出（非 JSON），temperature=0

### [v7: 新增] extractor: 纯 LLM 提取
- KnowledgeObject 输出格式（topic, attributes, source, confidence）
- 规则：omit 缺失 key 而非填空值，属性值可为字符串/列表/数字
- 纯 JSON 输出

### [v7: 修改] generator: knowledge_objects 优先
- 规则新增："knowledge_objects 是结构化知识，优先使用"
- "evidence 是扁平事实列表，仅在 knowledge_objects 不足时作为补充"

### [v7.1: 修改] planner: 顺序 DAG + 无数据依赖并行
- 对比类任务（"对比两个实验的差异"）改为顺序 DAG，避免并行 retrieval 数据丢失
- 并行 DAG 示例改为无数据依赖的独立分支（"查看文档数量和销售总额"）
- 新增说明：**并行任务之间不能有数据依赖**

### [v7.1: 修改] extractor: 长度和完整性约束
- 新增："属性值应使用完整的句子描述，确保信息完整可读，不要只写关键词"
- 新增："每个文档至少提取 3-5 个关键属性"
- 新增："如果文档内容较长，务必覆盖开头、中间、结尾的关键信息"

### [v7: 修改] critic: 移除 retrieval_report 完整性判断
- `retrieval_report` 仍传入用于审计，但不再作为证据覆盖度判断依据
- 不再有 `is_complete=false → retry_target="knowledge"` 的指引

---

## 新增 Agent 的步骤

1. 编写 Agent 类，继承 `BaseAgent`（或 `ControllerAgent` 如果是 Controller）
2. 定义 `capability`（role, outputs, merge_policy, control_actions 等）
3. 在 `agent_registry.py` 的 `create_default_registry()` 中实例化并注册

无需修改 `AgentContext`、`Orchestrator`、`Validator` 或 `Prompts.yaml`（Planner 通过 Registry 自动发现）。

---

## 模块职责 [v7: 修改]

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
- `agent_orchestrator.py` — Planner → TaskGraph → role 分派 Executor/Controller，[v7: input_mapping 解析 + BFS 自动注入]
- `agent_registry.py` — capability 注册 + 实例化 + prompt 生成 + 能力校验
- `workflow_validator.py` — WorkflowValidator + PolicyValidator 三层校验
- `agent_context.py` — outputs 容器 + 线程安全 + 按 task_id 隔离
- `agent_memory.py` — 会话记忆：事实提取、里程碑、偏好检测、LRU 淘汰

**Agent 实现:**
- `base_agent.py` — BaseAgent + ControllerAgent 基类
- `chat_agent.py` — 问候/闲聊，静态回复
- [v7: 新增] `retrieval_agent.py` — LLM 生成 query，代码控制 search_documents → read_all_rows → DocumentBundle
- [v7: 新增] `extraction_agent.py` — 纯 LLM，Map-Reduce 按文档分片并行提取 KnowledgeObject
- [v7: 删除] ~~`knowledge_agent.py`~~
- `analysis_agent.py` — 数值计算 + Tool Calling
- `critic_agent.py` — 继承 ControllerAgent，返回 ControlAction
- `answer_generator.py` — LLM 答案生成，[v7: 优先使用 knowledge_objects]

**MCP 协议层:**
- `client.py` — MCP Client（stdio + session 管理）
- `server.py` — MCP Server（常驻 + SessionManager）
- `session_manager.py` — Session 生命周期（创建/查询/删除/过期淘汰）
- `tools.py` — LangChain 工具封装（自动注入 session_id）

**Action 系统:**
- [v7: 新增] `actions/` — Action 注册与分发，新增 action type 只需注册 Handler

### models/ — 数据模型

- `capability.py` — AgentRole 枚举 + AgentCapability（outputs/merge_policy/role/control_actions）
- `control.py` — ControlAction 数据类
- `task_graph.py` — TaskNode + TaskGraph（[v7: input_mapping 字段]）
- `mcp_session.py` — MCPSession（session_id, document_ids, search_ctx）
- `data_types.py` — [v7: 修改] +DocumentChunk, DocumentBundle, KnowledgeObject
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

## 修改注意事项 [v7: 修改]

| 修改项 | 文件位置 | 说明 |
|--------|----------|------|
| 文档切片参数 | `core/document_processor.py` | chunk_size, chunk_overlap |
| 搜索阈值 | `core/rag_engine.py` | SCORE_THRESHOLD (0.92) |
| Embedding/LLM 模型 | `.env` | EMBEDDING_*, LLM_* |
| API 路径 | `app/api/` | 需同步更新 Java 后端 |
| 搜索/计算限制 | `core/rag_engine.py` | search_count>2, agg_count>8 |
| 提示词模板 | `core/prompts.yaml` | [v7: retrieval/extractor/planner/generator/critic 均修改] |
| LLM 创建参数 | `core/llm_factory.py` | create_llm(temperature, max_tokens, timeout) |
| Agent 能力声明 | `models/capability.py` | AgentCapability (role/outputs/merge_policy) |
| 合并策略 | `models/capability.py` | merge_policy (dedup/replace/append) |
| 新增 Agent | `core/agent_registry.py` | create_default_registry() 中加一行 |
| [v7: 新增] input_mapping | `core/agent_orchestrator.py` | _run_plan_task 中的显式 mapping + BFS 自动注入 |
| [v7: 新增] Map-Reduce 提取 | `core/agents/extraction_agent.py` | _group_by_source + asyncio.gather + _extract_single_source |
| [v7: 新增] 代码控制完整性 | `core/agents/retrieval_agent.py` | search_documents 后无条件 read_all_rows |
| [v7: 新增] DocumentBundle | `models/data_types.py` | +DocumentChunk, DocumentBundle, KnowledgeObject |
| [v7: 删除] KnowledgeAgent | `core/agents/knowledge_agent.py` | 整个文件删除 |
| [v7: 修改] Generator 数据源 | `core/generator/answer_generator.py` | knowledge_objects 优先于 evidence |
| DAG 结构校验 | `core/workflow_validator.py` | validate_structure() + get_layers() |
| Agent 能力校验 | `core/agent_registry.py` | validate_capabilities(plan, layers) |
| Controller 策略 | `core/workflow_validator.py` | PolicyValidator.validate_controller_usage() |
| 数据流校验 | `core/workflow_validator.py` | DAGDataFlowValidator.validate_input_mapping() — [v7.1: inputs 覆盖完整性校验] |
| 去重键规则 | `core/agent_orchestrator.py` | _dedup_key() per output key（兼容保留，热路径未使用） |
| 记忆淘汰策略 | `core/agent_memory.py` | idle_ttl, max_sessions, REWRITE_INTERVAL |
| MCP session TTL | `core/mcp/session_manager.py` | SessionManager._TTL (默认 1800s) |

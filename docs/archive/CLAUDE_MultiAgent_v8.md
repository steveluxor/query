# Query - Agent Runtime 架构 (v8: BFS 删除 + port_bindings 数据契约)

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

## 从 v7 到 v8 的变更

### 核心变更

| 变更 | v7 | v8 |
|------|----|----|
| 数据传递 | BFS 自动注入（同名匹配） + input_mapping 并存 | 仅 port_bindings（BFS 已删除） |
| Agent 输入声明 | `inputs: dict[str, type]`（部分 Agent 有） | 所有 Agent 统一声明 inputs + required_inputs |
| Agent 参数名 | 必须与上游 output_key 同名（通过 BFS） | 自由命名，由 port_bindings 桥接 |
| Validator 数据流校验 | 校验 input_mapping 覆盖完整性 | 校验 port_bindings 类型兼容性 + 端口完整性 |
| tool_descriptions | 无 | 各 Agent 在 capability 中声明工具中文描述，Orchestrator 统一映射 |
| dedup_key_func | 无 | 各 Agent 在 capability 中声明去重 key 提取函数，Runtime 自动合并 |
| Chat Agent | 硬编码静态回复 | LLM 化（create_llm），支持偏好注入 |
| Critic Agent | BaseAgent 子类 | ControllerAgent 子类，返回 ControlAction |
| Generator source_meta | `list` | `list[dict]`（修复自动绑定类型匹配） |
| Auto-wire | 无 | Planner 未生成 port_bindings 时，Orchestrator 按类型兼容自动补齐 |
| Plan 后处理 | 无 | `_post_process_plan` 兜底修正：数值问题强制加 analysis、纯数值移除 extractor |
| 记忆管理 | `update()` 单次调用 | `update()` + `append_turn()` 双写（recent_history 缓存） |
| ActionRegistry | 单文件 `actions.py` | 模块拆分 `actions/`（base + retry + terminate） |

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
```

### 未采纳的原计划

v8 设计阶段曾计划引入 `DataRequirement` 纯类型声明替代 `inputs: dict[str, type]`，
但实现中发现 `inputs` + `required_inputs` 的模式已足够表达数据契约，
且 `port_bindings` 直接按端口名匹配的方式更简洁（Validator 按端口名查类型，无需做类型→端口的反向映射）。

最终采用方案：保留 `inputs`，新增 `required_inputs` 标注必连端口。

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
    │   ├── ingestion.py         # 文档向量化 API
    │   └── qa.py                # 问答 API
    ├── core/
    │   ├── agent_context.py     # Agent 共享上下文
    │   ├── agent_memory.py      # 会话记忆管理
    │   ├── agent_orchestrator.py # Agent 编排器
    │   ├── agent_registry.py    # Agent 能力注册表
    │   ├── document_processor.py # 文档解析/切片
    │   ├── rag_engine.py        # RAG 核心引擎
    │   ├── workflow_validator.py # DAG 校验器（六层）
    │   ├── utils.py             # 工具函数 (extract_json)
    │   ├── log_config.py        # JSON 结构化日志
    │   ├── infra/               # 基础设施层
    │   │   ├── redis_store.py   # Redis 读取封装
    │   │   ├── vector_store.py  # 向量数据库
    │   │   └── llm_factory.py   # LLM 工厂函数
    │   ├── prompts/             # 提示词管理
    │   │   ├── prompt_manager.py # 提示词管理器
    │   │   └── prompts.yaml     # 提示词模板
    │   ├── actions/             # Action 注册与分发（模块化）
    │   │   ├── __init__.py      # ActionRegistry + create_default
    │   │   ├── base.py          # ActionHandler 抽象基类
    │   │   ├── retry.py         # RetryHandler
    │   │   └── terminate.py     # TerminateHandler
    │   ├── mcp/
    │   │   ├── client.py        # MCP Client
    │   │   ├── server.py        # MCP Server
    │   │   ├── session_manager.py # Session 管理
    │   │   └── tools.py         # LangChain 工具封装
    │   ├── agents/
    │   │   ├── base_agent.py    # BaseAgent + ControllerAgent 基类
    │   │   ├── chat_agent.py    # 问候/闲聊（LLM 化）
    │   │   ├── retrieval_agent.py  # 知识检索（代码控制搜索 + 全量加载）
    │   │   ├── extraction_agent.py # 知识提取（纯 LLM，Map-Reduce）
    │   │   ├── analysis_agent.py   # 数据分析
    │   │   └── critic_agent.py     # 答案审核 (ControllerAgent)
    │   └── generator/
    │       └── answer_generator.py # 答案生成
    └── models/
        ├── capability.py        # AgentCapability（含 dedup_key_func）
        ├── control.py           # ControlAction
        ├── task_graph.py        # TaskGraph（含 port_bindings）
        ├── mcp_session.py       # MCPSession
        ├── data_types.py        # Evidence, AnalysisResult, DocumentBundle 等
        └── schemas.py           # Pydantic 模型
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

### inputs + required_inputs 设计

`inputs` 声明 Agent 需要从上游接收的数据端口（参数名 → 类型），`required_inputs` 标注哪些端口必须有 binding。

**工作流程：**
1. Planner 生成 TaskGraph 时，为每个 task 写 `port_bindings`（显式声明"从哪个上游 task 的哪个 output 取数据"）
2. 如果 Planner 漏写，Orchestrator 的 `_auto_wire_port_bindings()` 按类型兼容性自动补齐
3. DAGDataFlowValidator 校验：required_inputs 中所有端口必须有 binding，类型必须兼容
4. 执行时 Orchestrator 按 port_bindings 从上游 task 取数据，通过 `**upstream_kwargs` 传给 Agent 的 `run()` 方法

**Agent 间解耦：** Agent 只声明"我需要什么"，不知道谁给。两个 Agent 的参数名可以不同，由 port_bindings 桥接：

```
Extractor 输出:  "evidence"     (output key)
Generator 输入:  "evidence_list" (input port)
                         ↑ port_bindings 桥接
```

### merge_policy 策略

| 策略 | 语义 | 适用 |
|------|------|------|
| `"replace"` | 直接替换（默认） | analysis, answer, critique |
| `"dedup"` | 去重合并（需配合 dedup_key_func） | evidence, sources |
| `"append"` | 直接追加 | document_bundle, knowledge_objects |

### dedup_key_func

Agent 在 capability 中声明去重 key 提取函数，Runtime 自动合并时使用：

```python
# ExtractionAgent
dedup_key_func={
    "evidence": lambda item: (getattr(item, 'source', ''), getattr(item, 'statement', '')[:200]),
    "sources": lambda item: (item.get("file_name", ""), str(item)[:200]),
}
```

### 各 Agent Capability 一览

| Agent | inputs | required_inputs | outputs | tools | tool_descriptions | merge_policy |
|-------|--------|----------------|---------|-------|-------------------|--------------|
| Retrieval | `{}` | `set()` | document_bundle, retrieval_report | search_documents, read_all_rows | 文档检索, 读取完整数据 | document_bundle=append |
| Extractor | `{"knowledge_document": DocumentBundle}` | `{"knowledge_document"}` | knowledge_objects, evidence, sources | 无 | 无 | knowledge_objects=append, evidence=dedup, sources=dedup |
| Analysis | `{}` | `set()` | analysis | calculate_sum, calculate_rank | 数值求和, 数值排名 | analysis=replace |
| Generator | `{"structured_knowledge": list[KnowledgeObject], "evidence_list": list[Evidence], "source_meta": list[dict], "analysis_result": AnalysisResult \| None}` | `{"structured_knowledge", "evidence_list", "source_meta"}` | answer | 无 | 无 | answer=replace |
| Critic (ControllerAgent) | `{"evidence_list": list, "generated_answer": str, "retrieval_report": RetrievalReport, "analysis_result": AnalysisResult \| None}` | `{"evidence_list", "generated_answer", "retrieval_report"}` | critique, need_retry, retry_target | 无 | 无 | critique=replace, need_retry=replace, retry_target=replace |
| Chat (LLM) | `{}` | `set()` | answer | 无 | 无 | — |

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

---

## AgentRegistry — 能力注册表

```python
class AgentRegistry:
    def register(capability, instance=None)           # 注册 Capability + 实例
    def get(name)                                     # 获取 Capability
    def get_agent(name)                               # 获取 Agent 实例
    def all_capabilities()                            # 所有 Capability
    def valid_names()                                 # 所有 Agent 名称
    def find_executors()                              # 所有 Executor（无 control_actions）
    def find_controllers()                            # 所有 Controller（有 control_actions）
    def find_by_tool(tool_name)                       # 拥有指定工具的 Agent
    def find_by_writes(field_name)                    # 写入指定 output 的 Agent
    def validate_capabilities(plan, layers)           # Agent 能力校验
    def format_executors_for_prompt()                 # Planner prompt: Executor 列表
    def format_controllers_for_prompt()               # Planner prompt: Controller 列表
```

### create_default_registry(rag_engine=None)

注册 6 个 Agent（Chat, Retrieval, Extractor, Analysis, Critic, Generator），内部完成 import + 实例化 + 注册。

### Agent 能力校验

`validate_capabilities(plan, layers)` 校验三项：

| 校验项 | 说明 | 错误示例 |
|--------|------|----------|
| Agent 注册检查 | task.agent 是否在 Registry 中 | `Agent 'unknown' 未注册` |
| 输出冲突检测 | 同层多 task 写同一 output_key（警告） | `输出冲突: [t1, t2] 都写入 'evidence'` |
| control_actions 契约 | Planner 不应指定 control action | `Planner 不应指定 control action` |

---

## TaskGraph — DAG 任务图

```python
@dataclass
class TaskNode:
    id: str                      # "task1"
    agent: str                   # "retrieval" / "extractor" / "analysis" / "generator"
    objective: str               # "获取2024销售数据"
    depends_on: list[str] = field(default_factory=list)
    output_key: str = ""         # 本任务输出标识
    port_bindings: dict[str, str] = field(default_factory=dict)
    # key   = Agent 输入端口名（= Agent run() 的参数名）
    # value = "source_task_id.output_key" 全限定引用
    status: TaskStatus = TaskStatus.PENDING  # pending/running/completed/failed/skipped/retrying

@dataclass
class TaskGraph:
    goal: str = ""               # "分析销售下降原因"
    goal_outputs: list[str] = field(default_factory=list)  # 期望产出（如 ["answer"]）
    tasks: list[TaskNode] = field(default_factory=list)

    def get_descendants(task_id) -> set[str]         # BFS 查找所有下游
    def invalidate_subgraph(task_ids) -> set[str]    # 标记 retrying
```

### port_bindings — 唯一数据通道

BFS 自动注入已删除。`port_bindings` 是 Runtime 唯一的 task 间数据传递通道：

```python
# v7 — BFS 注入逻辑（已删除）
visited = set()
queue = list(task.depends_on)
while queue:
    dep_id = queue.pop(0)
    ...
    for output_key in dep_cap.output_keys:
        entry = context.get_output_entry(output_key, task_id=dep_id)
        if entry and entry.value is not None:
            upstream_kwargs[output_key] = entry.value  # ← 同名注入
    ...

# v8 — 仅 port_bindings
for port_name, source_ref in task.port_bindings.items():
    if "." not in source_ref:
        continue
    source_task_id, output_key = source_ref.split(".", 1)
    entry = context.get_output_entry(output_key, task_id=source_task_id)
    if entry and entry.value is not None:
        upstream_kwargs[port_name] = entry.value  # ← 端口名 = 参数名
```

### Auto-wire 机制

当 Planner 未生成 `port_bindings` 时，`_auto_wire_port_bindings()` 按 capability 声明的类型兼容性自动补齐：

1. 遍历每个 task 的 `inputs` 端口
2. 跳过已有 binding 的端口
3. 在 `depends_on` 的上游 task 中查找 `outputs` 类型兼容的 output
4. 自动建立绑定并记录日志

```python
# auto-wire 示例
task2 (extractor) 的 inputs={"knowledge_document": DocumentBundle}
task1 (retrieval) 的 outputs={"document_bundle": DocumentBundle, ...}

# auto-wire 自动建立:
task2.port_bindings["knowledge_document"] = "task1.document_bundle"
# 类型 DocumentBundle == DocumentBundle ✓
```

### subgraph invalidation 示例

```
Retrieval (task1) → Extractor (task2) → Generator (task3) → Critic (task4)
                                               │
                                               └── Summary (task5, 独立分支)

Critic 返回 retry_target="retrieval":
  RetryHandler 按 agent 名查找 task1
  → invalidate_subgraph({"task1"})
  → 重置 task1, task2, task3, task4 为 retrying
  → task5 不受影响（独立分支）
```

---

## Validator 类型兼容性校验

### _type_matches()

```python
@staticmethod
def _type_matches(declared_type: type, expected_type: type) -> bool:
    if declared_type is expected_type:
        return True          # 精确匹配: DocumentBundle == DocumentBundle

    # Union/Optional: X | None 中的 X
    union_origin = typing.get_origin(expected_type)
    if union_origin is types.UnionType:
        return any(declared_type is arg for arg in typing.get_args(expected_type))

    # 参数化泛型: list[KnowledgeObject] == list[KnowledgeObject]
    declared_origin = typing.get_origin(declared_type)
    expected_origin = typing.get_origin(expected_type)
    if declared_origin is not None and expected_origin is not None:
        return (declared_origin is expected_origin
                and typing.get_args(declared_type) == typing.get_args(expected_type))

    # 参数化泛型 → bare 类型: list[dict] 满足 list
    if declared_origin is not None and expected_origin is None:
        return declared_origin is expected_type

    return False
```

### 校验流程（DAGDataFlowValidator.validate_port_bindings）

1. **格式**：source_ref 必须含 `task_id.` 前缀
2. **存在性**：引用的上游 task 必须在 DAG 中
3. **上游关系**：引用的 task 是当前 task 的上游祖先
4. **output_key 存在**：上游 Agent 的 outputs 包含对应 key
5. **端口存在 + 类型兼容**：port_bindings key 在 Agent inputs 中声明，且 source output 类型与 port 声明的类型兼容
6. **必选端口完整**：required_inputs 中所有 port 必须有 binding

---

## Plan 后处理 (_post_process_plan)

LLM 可能不遵守 Planner prompt 规则，代码层兜底修正：

```python
def _post_process_plan(self, plan, question, memory_context):
    lower_q = question.strip().lower()
    has_analysis = any(t.agent == "analysis" for t in plan.tasks)

    sum_keywords = ("花了多少钱", "总共", "合计", "总金额", "总和", "求和", "sum", "total", "一共")
    rank_keywords = ("最贵", "最便宜", "排名", "排序", "第", "最高", "最低", "rank", "top")

    needs_sum = any(kw in lower_q for kw in sum_keywords)
    needs_rank = any(kw in lower_q for kw in rank_keywords)
```

### 规则 1：数值问题强制 analysis

检测到求和/排名关键词但 plan 中无 analysis → 自动插入 analysis task，连接到 retrieval 下游，并加入 generator 的 depends_on。

### 规则 2：纯数值移除 extractor

求和/排名问题不需要 extractor 的文本提取（Extractor 耗时 90-160s）→ 移除所有 extractor task。

### 规则 3：清理失效 port_bindings

移除 extractor 后，清理所有引用已移除 task ID 的 depends_on 和 port_bindings。

---

## 工具描述 — tool_descriptions

Planner 的 `memory_context` 中需要看到可读的工具描述而非原始工具名。

实现方式：每个 Agent 在 capability 中声明 `tool_descriptions`，Orchestrator 的 `_update_memory()` 通过 registry 统一收集映射：

```python
def _update_memory(self, context):
    tool_desc_map = {}
    for cap in self.registry.all_capabilities():
        tool_desc_map.update(getattr(cap, 'tool_descriptions', {}))

    mapped_tools = [tool_desc_map.get(t, t) for t in context.tools_called]

    self.agent_memory.update(context.session_id, {
        "tools_called": mapped_tools,
        ...
    })
```

这样新增工具时只需在对应 Agent 的 capability 中加一行声明，无需修改其他文件。

---

## Agent 清单

| Agent | 文件 | 角色 | 职责 | LLM 调用方式 |
|-------|------|------|------|-------------|
| Chat | `agents/chat_agent.py` | EXECUTOR | 问候/闲聊/偏好设置 | 单次 LLM 调用（支持偏好注入） |
| Retrieval | `agents/retrieval_agent.py` | EXECUTOR | LLM 生成 query，代码控制搜索 + 无条件 read_all_rows → DocumentBundle | 单次 LLM 调用生成 query |
| Extractor | `agents/extraction_agent.py` | EXECUTOR | Map-Reduce：按文档分片并行 LLM 提取 KnowledgeObject | 每文档 1 次 LLM 调用（并行） |
| Analysis | `agents/analysis_agent.py` | EXECUTOR | 数值计算（求和/排名） | Tool Calling（计算最多 8 次） |
| Generator | `generator/answer_generator.py` | EXECUTOR | 答案生成，知识对象 + 证据 + 分析结果 | 单次 LLM 调用 |
| Critic | `agents/critic_agent.py` | **CONTROLLER** | 答案质量审核，返回 ControlAction | 单次 LLM 调用 |

### ChatAgent — LLM 化

v8 将 ChatAgent 从硬编码静态回复改为 LLM 驱动，支持偏好注入：

```python
class ChatAgent(BaseAgent):
    capability = AgentCapability(
        name="chat",
        outputs={"answer": str},
    )

    async def run(self, context, **kwargs):
        llm = create_llm(temperature=0.1, max_tokens=512)
        prefs_text = ""
        if context.preferences:
            prefs_text = f"\n用户偏好：{json.dumps(context.preferences, ensure_ascii=False)}"
        prompt = f"你是一个智能问答助手。...{prefs_text}\n\n用户说：{context.question}"
        resp = llm.invoke(prompt)
        context.set_output("answer", resp.content.strip(), producer="chat")
```

### RetrievalAgent — 代码控制搜索

LLM 只生成搜索关键词和查询类型（aggregation/comparison），代码控制搜索流程：

```
RetrievalAgent.run()
  ├── 1. LLM 生成 search query + 查询类型（temperature=0）
  │     └── aggregation（求和/排名/行号）→ strict 策略（单文档）
  │     └── comparison（对比/查询/总结）→ standard 策略（多文档）
  ├── 2. 代码调 search_documents(query, strategy)
  ├── 3. 代码无条件调 read_all_rows()  ← 代码控制，无 LLM 决策
  └── 4. 解析为 DocumentBundle → set_output("document_bundle", bundle)
```

### ExtractionAgent — Map-Reduce 提取

Extractor 采用 Map-Reduce 策略防止多文档时 LLM 遗漏部分文档：

```
DocumentBundle (152 chunks from 6 docs)
  │
  ├─ Map: 按 source 分组，每组独立并行调 LLM
  │   ├─ 实验二 (40 chunks) → LLM → [KO₁, KO₂, ...]
  │   ├─ 实验七 (30 chunks) → LLM → [KO₃, ...]
  │   └─ ... (asyncio.gather 并行执行)
  │
  └─ Reduce: 合并所有结果
       └─ knowledge_objects = KO₁ + KO₂ + ... (append)
       └─ evidence = ev₁ + ev₂ + ...           (dedup)
```

### 新数据类型

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
    attributes: dict = field(default_factory=dict)
    source: str = ""         # 来源文档
    confidence: float = 1.0  # 提取置信度
```

### Generator 数据源

Generator 的 prompt 同时包含 `knowledge_objects`（结构化摘要）和 `evidence`（原始细节），两者互补而非互斥：

```python
async def _generate(self, context, **kwargs):
    knowledge = kwargs.get("structured_knowledge") or []
    evidences = kwargs.get("evidence_list") or []
    sources = kwargs.get("source_meta") or []
    analysis = kwargs.get("analysis_result")

    prompt = self._build_prompt(context, evidence_list=evidences,
                                analysis_result=analysis, source_meta=sources,
                                structured_knowledge=knowledge)
```

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

### _run_plan_task — port_bindings 注入

```python
async def _run_plan_task(self, context, task, original_question):
    # 按 port_bindings 从指定上游 task 获取数据（唯一数据通道）
    upstream_kwargs = {}
    for port_name, source_ref in task.port_bindings.items():
        if "." not in source_ref:
            continue
        source_task_id, output_key = source_ref.split(".", 1)
        entry = context.get_output_entry(output_key, task_id=source_task_id)
        if entry and entry.value is not None:
            upstream_kwargs[port_name] = entry.value

    context.current_task_id = task.id
    _task_id_var.set(task.id)
    context.question = task.objective

    try:
        result = await agent.execute(
            context, task_id=task.id,
            mcp_client=self.mcp_client, mcp_session_id=context.mcp_session_id,
            original_question=original_question,
            **upstream_kwargs,    # port_bindings 解析后的上游数据
        )

        # outputs → context（由 Runtime 写入，Agent 只管返回）
        for key, value in result.outputs.items():
            if value is not None:
                context.set_output(key, value, producer=task.agent)

        # actions → ActionRegistry
        for action in result.actions:
            await self.action_registry.handle(action, context, self)

        task.status = TaskStatus.COMPLETED
    except Exception as e:
        task.status = TaskStatus.FAILED
        raise
    finally:
        context.question = original_question
```

### _plan — Planner 生成 TaskGraph

```python
def _plan(self, question, memory_context=None, history=None):
    prompt = PromptManager.get("planner", "system")
    prompt = prompt.replace("{available_executors}", self.registry.format_executors_for_prompt())
    prompt = prompt.replace("{available_controllers}", self.registry.format_controllers_for_prompt())

    # 注入可用文档列表
    doc_names = self.rag_engine.vector_store.get_document_names()
    if doc_names:
        doc_list = "\n".join(f"- [{did}] {name}" for did, name in sorted(doc_names.items()))
        prompt += f"\n\n可用文档：\n{doc_list}"

    # 注入长期记忆
    if memory_context:
        prompt += f"\n\n长期记忆：{memory_context}"

    # 注入最近对话历史（帮助 Planner 理解追问上下文）
    if history and len(history) > 0:
        recent = history[-5:]
        history_lines = []
        for h in recent:
            hq, ha = h.get('question', ''), h.get('answer', '')
            history_lines.append(f"用户: {str(hq)[:100]}\n助手: {str(ha)[:200]}")
        prompt += "\n\n最近对话历史：\n" + "\n---\n".join(history_lines)

    # 短追问 + 数值计算上下文 → 补全问题
    if len(question.strip()) < 10 and memory_context and (
        "数值求和" in memory_context or "数值排名" in memory_context
    ):
        continuation_kw = ("结果", "继续", "然后", "接着", "答案", "汇总", "总结")
        if any(kw in question.strip().lower() for kw in continuation_kw):
            question = f"{question}（这是对上一轮数值计算的简短追问。需要完整链路）"

    prompt += f"\n\n用户问题：{question}"
    # → LLM 生成 TaskGraph JSON → _parse_task_graph → _validate_task_graph
```

### _fallback_plan

```python
# 问候类 — Chat Agent 直接回答
TaskGraph(
    goal="",
    goal_outputs=["answer"],
    tasks=[TaskNode(id="task1", agent="chat", objective=question)],
)

# 非问候类 — retrieval → extractor → generator
TaskGraph(
    goal="",
    goal_outputs=["answer"],
    tasks=[
        TaskNode(id="task1", agent="retrieval", objective=question),
        TaskNode(id="task2", agent="extractor", objective=question,
                 depends_on=["task1"],
                 port_bindings={"knowledge_document": "task1.document_bundle"}),
        TaskNode(id="task3", agent="generator", objective=question,
                 depends_on=["task2"],
                 port_bindings={
                     "structured_knowledge": "task2.knowledge_objects",
                     "evidence_list": "task2.evidence",
                     "source_meta": "task2.sources",
                 }),
    ],
)
```

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

### 典型 DAG 示例

**问候/闲聊：**
```
Chat
```

**单步查询：**
```
Retrieval → Extractor → Generator
```

**查询 + 计算：**
```
Retrieval → Analysis → Generator
```

**查询 + 计算 + 审核：**
```
Retrieval → Analysis → Generator → Critic
```

**跨文档对比（顺序 DAG）：**
```
Retrieval(搜全部) → Extractor(提取全部) → Generator(对比)
```

**组合 DAG（检索→分析+提取→生成）：**
```
task1 (retrieval) ──→ task2 (analysis)  ──→ task4 (generator)
                    └─→ task3 (extractor) ─┘

port_bindings:
  task2: {}                                    # analysis 无 inputs
  task3: {"knowledge_document": "task1.document_bundle"}
  task4: {"structured_knowledge": "task3.knowledge_objects",
          "evidence_list": "task3.evidence",
          "source_meta": "task3.sources",
          "analysis_result": "task2.analysis"}
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
    # 工具名 → 描述映射
    tool_desc_map = {}
    for cap in self.registry.all_capabilities():
        tool_desc_map.update(getattr(cap, 'tool_descriptions', {}))
    mapped_tools = [tool_desc_map.get(t, t) for t in context.tools_called]

    self.agent_memory.update(context.session_id, {...})
    self.agent_memory.append_turn(context.session_id, question, answer)  # recent_history 缓存
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
```

### 核心约束

- **一个请求 = 一个 MCP session**，所有 Agent（含 Controller retry）共享同一个 session
- **session_id 只存在于 AgentContext**，MCPClient 不持有
- **Agent 不感知 session**，tool 签名中无 session_id（由 `tools.py` 自动注入）

### MCP Server — 工具清单

| 工具 | 参数 | 说明 |
|------|------|------|
| `set_document_ids` | session_id, ids | 设置文档权限 |
| `search_documents` | session_id, query, row_start?, row_end? | 搜索文档 |
| `list_documents` | session_id | 列出可检索文档 |
| `calculate_sum` | session_id, key_name, row_filter?, content_filter? | 求和 |
| `calculate_rank` | session_id, key_name, ascending, position?, content_filter? | 排名 |
| `read_all_rows` | session_id | 读取完整数据 |

---

## 模块职责

### core/ — 核心业务层

**基础设施:**
- `llm_factory.py` — `create_llm(temperature, max_tokens, timeout)` 统一创建 ChatOpenAI
- `utils.py` — `extract_json(text)` 从 LLM 输出提取 JSON（支持 markdown 代码块、大括号提取）
- `log_config.py` — JSON 结构化日志

**业务模块:**
- `document_processor.py` — 文档解析/切片（1000字符/200重叠），支持 PDF/DOCX/DOC/TXT/MD/XLSX
- `vector_store.py` — Chroma + Ollama Embeddings 封装
- `rag_engine.py` — RAG 核心引擎：搜索(60→0.92→关键词→多样性)、算法计算(不调 LLM)

**Agent Runtime:**
- `agent_orchestrator.py` — Planner → TaskGraph → auto-wire → port_bindings 注入 → 执行 → 记忆更新
- `agent_registry.py` — capability 注册 + 实例化 + prompt 生成 + 能力校验
- `workflow_validator.py` — WorkflowValidator + PolicyValidator + GoalValidator + DAGDataFlowValidator 六层校验
- `agent_context.py` — outputs 容器 + 线程安全 + 按 task_id 隔离 + contextvars
- `agent_memory.py` — 会话记忆：事实提取、里程碑、偏好检测、LRU 淘汰、restore_session、append_turn

**Agent 实现:**
- `base_agent.py` — BaseAgent + ControllerAgent 基类
- `chat_agent.py` — 问候/闲聊，LLM 化 + 偏好注入
- `retrieval_agent.py` — LLM 生成 query，代码控制 search_documents → read_all_rows → DocumentBundle
- `extraction_agent.py` — 纯 LLM，Map-Reduce 按文档分片并行提取 KnowledgeObject
- `analysis_agent.py` — 数值计算 + Tool Calling
- `critic_agent.py` — 继承 ControllerAgent，返回 ControlAction
- `answer_generator.py` — LLM 答案生成，knowledge_objects + evidence + analysis_result 同时使用

**MCP 协议层:**
- `client.py` — MCP Client（stdio + session 管理）
- `server.py` — MCP Server（常驻 + SessionManager）
- `session_manager.py` — Session 生命周期（创建/查询/删除/过期淘汰）
- `tools.py` — LangChain 工具封装（自动注入 session_id）

**Action 系统:**
- `actions/__init__.py` — ActionRegistry（注册表 + create_default）
- `actions/base.py` — ActionHandler 抽象基类
- `actions/retry.py` — RetryHandler（按 agent 名查找 task，invalidate_subgraph）
- `actions/terminate.py` — TerminateHandler

### models/ — 数据模型

- `capability.py` — AgentCapability（inputs/required_inputs/outputs/merge_policy/dedup_key_func/control_actions）
- `control.py` — ControlAction 数据类
- `task_graph.py` — TaskNode + TaskGraph（port_bindings + get_descendants + invalidate_subgraph）
- `mcp_session.py` — MCPSession（session_id, document_ids, search_ctx）
- `data_types.py` — Evidence, AnalysisResult, Calculation, CriticResult, AgentResult, DocumentBundle, DocumentChunk, KnowledgeObject, RetrievalReport, AgentTrace
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
| 提示词模板 | `core/prompts/prompts.yaml` | knowledge/retrieval/extractor/analysis/generator/critic/planner |
| LLM 创建参数 | `core/infra/llm_factory.py` | create_llm(temperature, max_tokens, timeout) |
| Agent 能力声明 | `models/capability.py` | AgentCapability (inputs/outputs/merge_policy/dedup_key_func) |
| 合并策略 | `models/capability.py` | merge_policy (dedup/replace/append) |
| 去重键规则 | `models/capability.py` | dedup_key_func per output key |
| 新增 Agent | `core/agent_registry.py` | create_default_registry() 中加 import + 注册 |
| 新增 Action | `core/actions/` | 写 Handler 子类 + create_default() 中注册 |
| Planner 规则 | `core/prompts/prompts.yaml` | planner.system |
| Plan 后处理 | `core/agent_orchestrator.py` | _post_process_plan() |
| Auto-wire | `core/agent_orchestrator.py` | _auto_wire_port_bindings() |
| 短追问补全 | `core/agent_orchestrator.py` | _plan() 中的 continuation_kw |
| DAG 结构校验 | `core/workflow_validator.py` | WorkflowValidator.validate_structure() |
| 能力校验 | `core/agent_registry.py` | validate_capabilities(plan, layers) |
| Controller 策略 | `core/workflow_validator.py` | PolicyValidator.validate_controller_usage() |
| port_bindings 校验 | `core/workflow_validator.py` | DAGDataFlowValidator.validate_port_bindings() |
| 记忆淘汰策略 | `core/agent_memory.py` | idle_ttl, max_sessions, REWRITE_INTERVAL |
| MCP session TTL | `core/mcp/session_manager.py` | SessionManager._TTL (默认 1800s) |

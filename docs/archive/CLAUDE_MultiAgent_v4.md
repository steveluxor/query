# Query - Agent Runtime 架构 (v4: Context 作为数据交换协议)

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

### 核心概念

```
Agent Registry (能力声明层)
     |
     | AgentCapability {inputs, outputs, merge_policy, tools}
     |
     v
AgentContext (数据交换协议)
     |
     | outputs: dict[str, AgentOutput]  ← 泛化数据交换容器
     | AgentOutput {value, producer, version, timestamp}
     |
     v
Orchestrator (生命周期管理)
     |
     | 1. 查 Registry → 获取 Agent Capability
     | 2. 校验 inputs 是否满足 (has_all_outputs)
     | 3. execute(Agent)
     | 4. retry: clear_outputs(output_keys) + merge(merge_policy)
```

**架构升级路线：**

```
v3 (Multi-Agent 应用):                  v4 (Agent Runtime 框架):
  AgentContext = 业务字段集合              AgentContext = 数据交换协议
  set_evidence/set_sources/...           set_output(key, value, producer)
  context.evidence → 硬编码访问           context.get_output("evidence")
  Orchestrator 知道 Agent 内部结构         Orchestrator 只读 Registry
  AGENT_RESET_FIELDS 静态映射             reset = Capability.output_keys
  新增 Agent 改 3+ 文件                   新增 Agent = 1 个类 + 注册
  _merge_evidence 硬编码                  _merge_outputs 通用合并 (dedup/replace/append)
  无元数据                                producer + version + timestamp
  无线程安全                              RLock 保护 outputs
```

---

## 项目结构

```
query/
├── pyproject.toml               # 项目依赖配置 + pytest 配置
├── .env                         # 环境变量 (API Key, 模型配置)
├── Dockerfile
├── docker-compose.yml
└── app/
    ├── main.py                  # FastAPI 入口 (初始化所有组件)
    ├── config.py                # 配置管理 (读取 .env)
    ├── exceptions.py            # 业务异常定义
    ├── stream_consumer.py       # RabbitMQ 消费者 (异步文档向量化)
    ├── api/
    │   ├── ingestion.py         # 文档向量化 API
    │   └── qa.py                # 问答 API (Multi-Agent 入口)
    ├── core/
    │   ├── llm_factory.py       # LLM 工厂函数 (统一创建 ChatOpenAI)
    │   ├── utils.py             # 工具函数 (extract_json)
    │   ├── log_config.py        # JSON 结构化日志配置
    │   ├── document_processor.py # 文档解析/切片
    │   ├── vector_store.py      # 向量数据库封装
    │   ├── rag_engine.py        # RAG 核心引擎 (搜索/计算逻辑, 无状态)
    │   ├── agent_context.py     # Agent 间共享上下文 (outputs 容器 + 线程安全)
    │   ├── agent_orchestrator.py # Agent 编排器 (通用 Merge Runtime + Registry 调度)
    │   ├── agent_registry.py    # Agent 能力注册表 (DAG 校验 + 实例绑定)
    │   ├── agent_memory.py      # 会话记忆管理 (线程安全, 事实/偏好/里程碑)
    │   ├── redis_store.py       # Redis 读取封装 (只读, 写入由 Java 负责)
    │   ├── prompt_manager.py    # 提示词管理器
    │   ├── prompts.yaml         # 所有提示词模板
    │   ├── mcp/                 # MCP 协议层 (v4: Session 隔离)
    │   │   ├── client.py        # MCP Client (stdio + session 管理)
    │   │   ├── server.py        # MCP Server (常驻 + SessionManager)
    │   │   ├── session_manager.py # Session 生命周期管理 (create/get/delete/expire)
    │   │   └── tools.py         # LangChain 工具封装 (自动注入 session_id)
    │   ├── agents/
    │   │   ├── base_agent.py    # Agent 基类 (计时 + AgentCapability)
    │   │   ├── coordinator_agent.py  # 任务路由 (LLM 分类)
    │   │   ├── knowledge_agent.py    # 知识检索 (Tool Calling)
    │   │   ├── analysis_agent.py     # 数据分析 (Tool Calling)
    │   │   └── critic_agent.py       # 答案审核
    │   └── generator/
    │       └── answer_generator.py   # 答案生成
    └── models/
        ├── capability.py        # AgentCapability 数据类 (inputs/outputs/merge_policy)
        ├── task_graph.py        # TaskGraph DAG 数据类
        ├── mcp_session.py       # MCPSession 数据类 (per-session 状态隔离)
        ├── data_types.py        # AgentOutput, Evidence, AnalysisResult, CriticResult, AgentTrace
        └── schemas.py           # Pydantic 请求/响应模型
```

---

## AgentCapability — 数据契约 (v4: 新设计)

Agent 通过 `AgentCapability` 声明自己的输入、输出和合并策略，Orchestrator 只读 Registry 驱动生命周期：

```python
@dataclass
class AgentCapability:
    name: str                                    # "knowledge"
    description: str                             # "知识检索..."
    inputs: list[str]                            # ["evidence"] — 执行前必须存在的 output key
    outputs: dict[str, type]                     # {"evidence": list[Evidence]} — 写入的 key → 类型
    tools: list[str]                             # ["search_documents"]
    merge_policy: dict[str, str]                 # {"evidence": "dedup", "analysis": "replace"}

    @property
    def output_keys(self) -> list[str]:           # outputs 的所有 key
    @property
    def merged_keys(self) -> list[str]:           # merge_policy 为 dedup/append 的 key
```

### merge_policy 策略

| 策略 | 语义 | 适用 |
|------|------|------|
| `"replace"` | 直接替换（默认） | analysis, answer, critique |
| `"dedup"` | 去重合并 | evidence, sources |
| `"append"` | 直接追加 | logs, trace |

### 各 Agent Capability 一览

| Agent | inputs | outputs | merge_policy |
|-------|--------|---------|-------------|
| Knowledge | [] | evidence: list[Evidence], sources: list[dict] | evidence=dedup, sources=dedup |
| Analysis | [evidence] | analysis: AnalysisResult | analysis=replace |
| Generator | [evidence, analysis] | answer: str | answer=replace |
| Critic | [answer] | critique: str, need_retry: bool, retry_target: str | critique=replace, need_retry=replace, retry_target=replace |

---

## AgentOutput — 数据交换单位 (v4: 新增)

```python
@dataclass
class AgentOutput:
    value: Any                    # 实际数据值
    producer: str = ""            # 生产者 Agent 名称 ("knowledge")
    version: int = 1              # 写入次数（自动递增）
    timestamp: float = 0.0        # 写入时间（自动记录）
    metadata: dict = field(default_factory=dict)
```

每次 `set_output()` 自动维护 version 和 timestamp，重复写入时 version 递增。

---

## AgentContext — 数据交换协议 (v4: 重写)

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
    outputs: dict[str, AgentOutput] = field(default_factory=dict)

    # ── 执行轨迹 ──
    traces: list[AgentTrace] = field(default_factory=list)
    steps: list[AgentStep] = field(default_factory=list)
    start_time: float = field(default_factory=time.time)

    # ── 兼容字段 ──
    tools_called: list[str] = field(default_factory=list)
    is_agg: bool = False

    # ── 线程安全 ──
    _lock: RLock = field(default_factory=RLock, repr=False, compare=False)
```

### 通用 API

| 方法 | 说明 |
|------|------|
| `set_output(key, value, producer="")` | 线程安全设置，自动维护 version/timestamp |
| `get_output(key, default=None)` | 获取值（自动解包装 AgentOutput） |
| `get_output_entry(key)` | 获取完整 AgentOutput 元数据 |
| `clear_outputs(keys)` | 按列表清空（线程安全） |
| `has_output(key)` | 检查 key 是否存在 |
| `has_all_outputs(keys)` | 批量检查 |

### 线程安全

`outputs` 的读写由 `RLock` 保护，Agent 顺序执行（无并发竞争），锁用于防御性保护。

---

## AgentRegistry — 能力注册表

```python
class AgentRegistry:
    def register(capability, instance=None)    # 注册 Capability + 可选实例绑定
    def get(name)                              # 获取 Capability
    def get_agent(name)                        # 获取 Agent 实例
    def all_capabilities()                     # 获取所有 Capability
    def valid_names()                          # 获取所有 Agent 名称
    def find_by_tool(tool_name)                # 查找拥有指定工具的 Agent
    def find_by_writes(field_name)             # 查找写入指定 output 的 Agent
    def validate_dag(plan)                     # DAG 数据流校验
    def format_for_prompt()                    # 生成 planner prompt 片段
```

### DAG 校验

- Agent 注册检查
- 依赖存在性检查
- 循环依赖检测（拓扑排序）
- 输出冲突检测（同层任务写同一 output key）

### create_default_registry()

注册所有 4 个 Agent（Knowledge, Analysis, Critic, Generator），仅注册 Capability 不绑定实例。
实例在 Orchestrator 中通过 `registry.register(cap, instance)` 绑定。

---

## Multi-Agent 信息流

```
Question → Coordinator → Knowledge → Analysis(可选) → Generate → Critic(可选) → Answer
```

**简单模式**: Knowledge → Analysis(可选) → Generate → Critic(可选)
**规划模式**: Planner 拆多步 → 逐步执行(Knowledge/Analysis) → Evidence 累积 → Generate → Critic

Critic 重试机制: `retry_target` 控制重跑范围 (knowledge/analysis/generator/all)，最多 2 次。

---

## 通用 Merge Runtime (v4: 新增)

Orchestrator 提供 `_merge_outputs()` 通用合并方法，根据 AgentCapability.merge_policy 驱动：

```python
@staticmethod
def _merge_outputs(old_value, new_value, policy: str, output_key: str):
    if policy == "replace":    return new_value
    elif policy == "append":   return old_value + new_value  (仅 list)
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

## 通用 Retry 生命周期 (v4: 重写)

### reset_for_retry — 走 Registry

```python
def reset_for_retry(self, context, target):
    if target == "all":
        for cap in self.registry.all_capabilities():
            context.clear_outputs(cap.output_keys)
        context.is_agg = False
        context.tools_called = []
    elif target.startswith("task"):
        pass  # DAG 模式由 _retry_from_task 处理
    else:
        cap = self.registry.get(target)
        if cap:
            context.clear_outputs(cap.output_keys)
```

### 证据保留与合并

重试 knowledge 时保存旧 evidence/sources，执行后按 merge_policy 合并：

```python
saved = {}
cap = self.registry.get("knowledge")
for merge_key in cap.merged_keys:  # ["evidence", "sources"]
    v = context.get_output(merge_key)
    if v is not None:
        saved[merge_key] = v

# 执行 Agent ...

# 合并旧数据
for key, old_val in saved.items():
    new_val = context.get_output(key)
    if new_val is not None:
        policy = cap.merge_policy.get(key, "replace")
        merged = self._merge_outputs(old_val, new_val, policy, key)
        context.set_output(key, merged, producer="knowledge")
```

### 简单模式 Agent 调度 — 走 Registry

```python
retry_agents = set()
if target == "all":
    retry_agents = {"knowledge", "analysis"}
else:
    retry_agents.add(target)

for agent_name in sorted(retry_agents):
    instance = self.registry.get_agent(agent_name)
    if not instance:
        continue
    cap = self.registry.get(agent_name)
    if cap and cap.inputs and not context.has_all_outputs(cap.inputs):
        logger.warning("跳过 %s: 缺少 %s", agent_name, cap.inputs)
        continue
    await instance.execute(
        context, mcp_client=self.mcp_client, mcp_session_id=context.mcp_session_id,
    )
```

---

## Agent 清单

| Agent | 文件 | 职责 | LLM 调用方式 | Capability |
|-------|------|------|-------------|------------|
| Coordinator | `agents/coordinator_agent.py` | 问题分类 (needs_plan/needs_analysis/needs_review) | 单次调用, 输出 JSON | — |
| Knowledge | `agents/knowledge_agent.py` | 知识检索 + 简单问题回答 | Tool Calling 循环 (搜索最多 2 次) | inputs=[], outputs={evidence, sources}, merge=dedup |
| Analysis | `agents/analysis_agent.py` | 数值计算 (求和/排名) | Tool Calling 循环 (计算最多 8 次) | inputs=[evidence], outputs={analysis}, merge=replace |
| Generator | `generator/answer_generator.py` | 答案生成 | 单次调用, 输出自然语言 | inputs=[evidence, analysis], outputs={answer}, merge=replace |
| Critic | `agents/critic_agent.py` | 答案质量审核 | 单次调用, 输出 verdict JSON | inputs=[answer], outputs={critique, need_retry, retry_target}, merge=replace |

---

## MCP 架构 (v4: Session 隔离)

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
  +-- session_A { search_ctx, document_ids, analysis_ctx }
  +-- session_B { search_ctx, document_ids, analysis_ctx }
       |
RAGEngine (无状态, 参数传入 SearchContext/AnalysisContext)
```

### 核心约束

- **一个请求 = 一个 MCP session**，所有 Agent（含 Critic retry）共享同一个 session
- **session_id 只存在于 AgentContext**，MCPClient 不持有
- **Agent 不感知 session**，tool 签名中无 session_id（由 `tools.py` 自动注入）
- **create_session / cleanup_session 是 MCPClient 内部方法**，不暴露为 Agent 可见的 tool

### MCP Server — 工具清单

| 工具 | 参数 | 说明 | 可见性 |
|------|------|------|--------|
| `_create_session` | session_id | 创建 session (内部) | 内部 |
| `_cleanup_session` | session_id | 清理 session (内部) | 内部 |
| `set_document_ids` | session_id, ids | 设置文档权限 | Agent 可见 |
| `search_documents` | session_id, query, row_start?, row_end? | 搜索文档 | Agent 可见 |
| `list_documents` | session_id | 列出可检索文档 | Agent 可见 |
| `calculate_sum` | session_id, key_name, row_filter?, content_filter? | 求和 | Agent 可见 |
| `calculate_rank` | session_id, key_name, ascending, position?, content_filter? | 排名 | Agent 可见 |
| `read_all_rows` | session_id | 读取完整数据行 | Agent 可见 |

---

## 模块职责

### api/ — 接口层

- `ingestion.py`:
  - `POST /ingest/document` — 接收文档内容, 解析、切片、向量化存入 Chroma
  - `DELETE /ingest/document/{id}` — 删除指定文档的向量
- `qa.py`:
  - `POST /qa/ask` — Multi-Agent 问答入口, 接收问题返回答案
  - 使用 `context.get_output("answer/sources")` 读取 Agent 输出

### core/ — 核心业务层

**基础设施:**

- `llm_factory.py` — LLM 工厂函数
  - `create_llm(temperature, max_tokens, timeout)` — 统一创建 ChatOpenAI 实例

- `utils.py` — 工具函数
  - `extract_json(text)` — 从 LLM 输出中提取 JSON

- `log_config.py` — 结构化日志
  - `JSONFormatter` — JSON 格式日志输出
  - `setup_logging()` — 配置 root logger

**业务模块:**

- `document_processor.py` — 文档处理器
  - 支持格式: PDF、DOCX、DOC、TXT、MD、XLSX
  - `RecursiveCharacterTextSplitter` 切片 (1000字符/200重叠)

- `vector_store.py` — 向量数据库封装
  - Chroma + Ollama Embeddings (nomic-embed-text)

- `rag_engine.py` — RAG 核心引擎 (无状态)
  - 搜索: Embedding 相似度(60) → 阈值过滤(0.92) → 关键词补充 → 多样性选取 → 文件名回退
  - 计算工具: `_sum_by_key`, `_rank_by_key` (纯算法, 不调 LLM)

- `agent_orchestrator.py` — Agent 编排器 (v4: Registry 调度 + 通用 Merge Runtime)
  - 通过 Registry 获取 Agent Capability 和实例，不再硬编码
  - `_merge_outputs()` 通用合并 + `_dedup_key()` 去重键生成
  - `reset_for_retry()` 通过 Registry Capability 驱动
  - Critic 审核失败重试 (最多 2 次)
  - MCP session 生命周期管理

- `agent_registry.py` — Agent 能力注册表
  - DAG 数据流校验 (循环检测、输出冲突、依赖存在性)
  - `create_default_registry()` 注册所有 4 个 Agent
  - 能力查找 (by_tool, by_writes via `cap.output_keys`)

- `agent_context.py` — Agent 间共享上下文 (v4: outputs 容器 + 线程安全)
  - 删除业务字段 (`evidence`, `sources`, `analysis`, `answer`, `critique` 等)
  - **通用 API**: `set_output/get_output/clear_outputs/has_all_outputs`
  - **元数据**: 每个 output 带 producer/version/timestamp
  - **线程安全**: `RLock` 保护 outputs dict

- `agent_memory.py` — 会话记忆管理
  - 线程安全 RLock 保护 `_sessions`
  - 事实提取、里程碑摘要、偏好检测、Fact 压缩

- `redis_store.py` — Redis 只读封装

### agents/ — Agent 实现

- `base_agent.py` — Agent 基类 (name, capability, execute 计时)
- `coordinator_agent.py` — 任务路由, LLM 三元分类
- `knowledge_agent.py` — 知识检索, `set_output("evidence/sources")`
- `analysis_agent.py` — 数据分析, `set_output("analysis")`
- `critic_agent.py` — 答案审核, `set_output("critique/need_retry/retry_target")`

### generator/ — 答案生成

- `answer_generator.py` — 答案生成, `set_output("answer")`
  - 使用 `context.get_output("evidence/analysis/sources")`

### models/ — 数据模型

- `capability.py` — `AgentCapability` (inputs, outputs, merge_policy, tools)
- `task_graph.py` — `TaskNode` + `TaskGraph` (DAG 任务图)
- `mcp_session.py` — `MCPSession` (session_id, document_ids, search_ctx, analysis_ctx)
- `data_types.py` — `AgentOutput`, `Evidence`, `Calculation`, `AnalysisResult`, `CriticResult`, `AgentTrace`
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

### 测试

```bash
python -m pytest tests/ -v
python -m pytest tests/test_agent_context.py -v
python -m pytest tests/test_agent_registry.py -v
python -m pytest tests/test_capability.py -v
python -m pytest tests/test_orchestrator_integration.py -v
```

---

## 新增 Agent 的步骤

1. 编写 Agent 类，继承 `BaseAgent`，定义 `capability` (inputs/outputs/merge_policy)
2. 在 `AgentCapability` 声明 `outputs` 和 `merge_policy`
3. 在 `agent_registry.py` 的 `create_default_registry()` 中注册
4. 在 `AgentOrchestrator.__init__()` 中绑定实例：`self.registry.register(cls.capability, instance)`

无需修改 `AgentContext`、`reset_for_retry` 或 `_merge_outputs`。

---

## 修改注意事项

| 修改项 | 文件位置 | 说明 |
|--------|----------|------|
| 文档切片参数 | `core/document_processor.py` | chunk_size, chunk_overlap |
| 搜索阈值 | `core/rag_engine.py` | SCORE_THRESHOLD (0.92) |
| Embedding/LLM 模型 | `.env` | EMBEDDING_*, LLM_* |
| API 路径 | `app/api/` | 需同步更新 Java 后端 |
| Agent 分类逻辑 | `core/agents/coordinator_agent.py` | CLASSIFY_SYSTEM prompt |
| 搜索/计算限制 | `core/rag_engine.py` | search_count>2, agg_count>8 |
| Critic 重试次数 | `core/agent_orchestrator.py` | MAX_CRITIC_RETRIES=2 |
| 提示词模板 | `core/prompts.yaml` | knowledge/analysis/generator/critic/planner |
| LLM 创建参数 | `core/llm_factory.py` | create_llm(temperature, max_tokens, timeout) |
| Agent 能力声明 | `models/capability.py` | AgentCapability (inputs/outputs/merge_policy) |
| 合并策略 | `models/capability.py` | merge_policy (dedup/replace/append) |
| DAG 校验逻辑 | `core/agent_registry.py` | validate_dag() |
| 去重键规则 | `core/agent_orchestrator.py` | _dedup_key() per output key |
| 记忆淘汰策略 | `core/agent_memory.py` | idle_ttl, max_sessions, REWRITE_INTERVAL |
| JSON 日志格式 | `core/log_config.py` | JSONFormatter |
| CORS 配置 | `.env` | CORS_ORIGINS 环境变量 |
| MCP session TTL | `core/mcp/session_manager.py` | SessionManager._TTL (默认 1800s) |

# Query - Multi-Agent RAG 知识库问答系统 (Python AI 服务)

## 系统架构

本项目是一个 **Multi-Agent RAG 智能知识库问答系统**，由三部分组成：

| 组件 | 路径 | 技术栈 | 端口 |
|------|------|--------|------|
| 前端 | `D:\DOWNLOAD\nginx-query` | Nginx + 原生 HTML/CSS/JS | :8080 |
| Java 后端 | `D:\IntelliJ IDEA 2025.1.3\project\Query` | Spring Boot 4.0.6 + MyBatis | :8085 |
| Python AI 服务 | 本项目 (`D:\DOWNLOAD\pycharm\query`) | FastAPI + LangChain + ChromaDB + MCP | :8000 |

**请求流向：** 前端(:8080) → Nginx 反向代理 → Java 后端(:8085) → Python AI 服务(:8000)

本项目是系统的 AI 核心，采用 Multi-Agent 架构处理文档向量化和 RAG 问答，由 Java 后端通过 HTTP 调用。

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
    │   ├── rag_engine.py        # RAG 核心引擎 (搜索/计算逻辑)
    │   ├── agent_context.py     # Agent 间共享上下文 (写保护)
    │   ├── agent_orchestrator.py # Agent 编排器 (调度所有 Agent)
    │   ├── agent_registry.py    # Agent 能力注册表 (DAG 校验)
    │   ├── agent_memory.py      # 会话记忆管理 (线程安全, 事实/偏好/里程碑)
    │   ├── redis_store.py       # Redis 读取封装 (只读, 写入由 Java 负责)
    │   ├── prompt_manager.py    # 提示词管理器
    │   ├── prompts.yaml         # 所有提示词模板
    │   ├── mcp/                 # MCP 协议层
    │   │   ├── client.py        # MCP Client (stdio 通信)
    │   │   ├── server.py        # MCP Server (工具执行进程)
    │   │   └── tools.py         # LangChain 工具封装
    │   ├── agents/
    │   │   ├── base_agent.py    # Agent 基类 (计时 + AgentCapability)
    │   │   ├── coordinator_agent.py  # 任务路由 (LLM 分类)
    │   │   ├── knowledge_agent.py    # 知识检索 (Tool Calling)
    │   │   ├── analysis_agent.py     # 数据分析 (Tool Calling)
    │   │   └── critic_agent.py       # 答案审核
    │   └── generator/
    │       └── answer_generator.py   # 答案生成
    └── models/
        ├── capability.py        # AgentCapability 数据类
        ├── task_graph.py        # TaskGraph DAG 数据类
        ├── data_types.py        # Evidence, Calculation, AnalysisResult, CriticResult, AgentTrace
        └── schemas.py           # Pydantic 请求/响应模型
```

---

## Multi-Agent 信息流

```
Question → Coordinator → Knowledge → Analysis(可选) → Generate → Critic(可选) → Answer
```

**简单模式**: Knowledge → Analysis(可选) → Generate → Critic(可选)
**规划模式**: Planner 拆多步 → 逐步执行(Knowledge/Analysis) → Evidence 累积 → Generate → Critic

Critic 重试机制: `retry_target` 控制重跑范围 (knowledge/analysis/generator/all)，最多 2 次。

### AgentContext 写保护

Agent 间通过 `AgentContext` 共享数据，采用**字段所有权保护**防止意外覆盖：

- `set_evidence(evidence)` — 非空时抛 RuntimeError（仅 Knowledge Agent 可写）
- `set_sources(sources)` — 非空时抛 RuntimeError（仅 Knowledge Agent 可写）
- `set_analysis(analysis)` — 非 None 时抛 RuntimeError（仅 Analysis Agent 可写）
- `set_answer(answer)` — 可覆盖（AnswerGenerator 写入）
- `set_critique(critique, need_retry, retry_target)` — 可覆盖（Critic 写入）
- `reset_for_retry(target)` — Orchestrator 调用，清空对应字段允许重新写入

---

## Agent 架构

### Agent 清单

| Agent | 文件 | 职责 | LLM 调用方式 | Capability |
|-------|------|------|-------------|------------|
| Coordinator | `agents/coordinator_agent.py` | 问题分类 (needs_plan/needs_analysis/needs_review) | 单次调用, 输出 JSON | — |
| Knowledge | `agents/knowledge_agent.py` | 知识检索 + 简单问题回答 | Tool Calling 循环 (搜索最多 2 次) | tools=[search_documents, list_documents, read_all_rows], writes_to=[evidence, sources] |
| Analysis | `agents/analysis_agent.py` | 数值计算 (求和/排名) | Tool Calling 循环 (计算最多 8 次) | tools=[calculate_sum, calculate_rank], writes_to=[analysis], requires=[evidence] |
| AnswerGenerator | `generator/answer_generator.py` | 答案生成 | 单次调用, 输出自然语言 | writes_to=[answer] |
| Critic | `agents/critic_agent.py` | 答案质量审核 | 单次调用, 输出 verdict JSON | — |

### AgentRegistry — 能力声明层

`agent_registry.py` 管理所有 Agent 的能力声明和实例绑定：

```python
registry = AgentRegistry()
registry.register(KnowledgeAgent.capability, instance=knowledge_agent)
registry.register(AnalysisAgent.capability, instance=analysis_agent)
```

核心方法：
- `register(capability, instance)` — 注册 Agent 能力 + 实例
- `get_agent(name)` — 获取已注册的 Agent 实例
- `find_by_tool(tool_name)` — 查找拥有指定工具的所有 Agent
- `find_by_writes(field_name)` — 查找写入指定 context 字段的所有 Agent
- `validate_dag(plan: TaskGraph)` — 校验 DAG 数据流合法性
  - Agent 注册检查
  - 依赖存在性检查
  - 输出冲突检测（同层任务写同一字段）
  - 循环依赖检测（拓扑排序）
- `format_for_prompt()` — 生成 prompt 片段供 Planner 使用

### AgentCapability — 能力声明

```python
@dataclass
class AgentCapability:
    name: str                    # "knowledge"
    description: str             # "知识检索，搜索文档、提取事实"
    tools: list[str]             # ["search_documents", "list_documents"]
    writes_to: list[str]         # ["evidence", "sources"]  写入 AgentContext 的哪些字段
    requires: list[str]          # ["evidence"]  执行前必须存在的 context 字段
```

### TaskGraph — DAG 任务图

```python
@dataclass
class TaskNode:
    id: str                      # "task1"
    agent: str                   # "knowledge" / "analysis"
    objective: str               # "获取2024销售数据"
    depends_on: list[str]        # ["task0"]
    output_key: str              # "sales_data"
    status: str                  # pending / running / completed / failed / skipped

@dataclass
class TaskGraph:
    goal: str                    # "分析销售下降原因"
    tasks: list[TaskNode]
```

Planner 生成 TaskGraph → Orchestrator 按拓扑排序逐步执行 → Critic 审核后决定是否重试。

---

## MCP 架构

系统采用 **MCP (Model Context Protocol)** 将工具执行分离到独立进程:

```
FastAPI 进程                          MCP Server 进程 (stdio)
┌─────────────────────┐              ┌─────────────────────┐
│  Knowledge Agent    │              │                     │
│  Analysis Agent     │──MCP Client──│  search_documents   │
│                     │              │  list_documents     │
│  LangChain Agent    │              │  calculate_sum      │
│  (tool calling)     │              │  calculate_rank     │
│                     │              │  read_all_rows      │
└─────────────────────┘              │                     │
                                     │  MCPState (共享状态) │
                                     │  SearchContext 缓存  │
                                     └─────────────────────┘
```

- `core/mcp/client.py` — Client 端, 通过 stdio 与 Server 通信
- `core/mcp/server.py` — Server 端, 注册工具并执行
- `core/mcp/tools.py` — 将 MCP 工具封装为 LangChain tools

---

## 模块职责

### api/ — 接口层

- `ingestion.py`:
  - `POST /ingest/document` — 接收文档内容, 解析、切片、向量化存入 Chroma
  - `DELETE /ingest/document/{id}` — 删除指定文档的向量
- `qa.py`:
  - `POST /qa/ask` — Multi-Agent 问答入口, 接收问题返回答案

### core/ — 核心业务层

**基础设施:**

- `llm_factory.py` — LLM 工厂函数
  - `create_llm(temperature, max_tokens, timeout)` — 统一创建 ChatOpenAI 实例
  - 消除了 rag_engine / agent_memory / critic_agent / answer_generator 中的4处重复创建

- `utils.py` — 工具函数
  - `extract_json(text)` — 从 LLM 输出中提取 JSON（直接解析 → markdown 代码块 → {} 块深度匹配提取）

- `log_config.py` — 结构化日志
  - `JSONFormatter` — JSON 格式日志输出
  - `setup_logging()` — 配置 root logger

**业务模块:**

- `document_processor.py` — 文档处理器
  - 支持格式: PDF、DOCX、DOC、TXT、MD、XLSX
  - Excel 每行视为独立 chunk, 不做文本切片
  - `RecursiveCharacterTextSplitter` 切片 (1000字符/200重叠)

- `vector_store.py` — 向量数据库封装
  - Chroma + Ollama Embeddings (nomic-embed-text)
  - 提供: add_texts, similarity_search, keyword_search, delete_document

- `rag_engine.py` — RAG 核心引擎
  - 搜索: Embedding 相似度(60) → 阈值过滤(0.92) → 关键词补充 → 多样性选取 → 文件名回退
  - 计算工具: `_sum_by_key`, `_rank_by_key` (纯算法, 不调 LLM)
  - 搜索限制: search_count > 2 停止; 计算限制: agg_count > 8 停止

- `agent_orchestrator.py` — Agent 编排器
  - 调度所有 Agent 的执行顺序
  - 管理 Plan-and-Execute 流程
  - Critic 审核失败重试 (最多 2 次, retry_target 控制重跑范围)
  - `_restore_memory()` / `_update_memory()` — 记忆恢复和更新

- `agent_registry.py` — Agent 能力注册表
  - DAG 数据流校验 (循环检测、输出冲突、依赖存在性)
  - 能力查找 (by_tool, by_writes)
  - 实例绑定

- `agent_context.py` — Agent 间共享上下文
  - 字段所有权保护 (set_evidence/set_sources/set_analysis 写保护)
  - `reset_for_retry(target)` — 根据 retry_target 清空对应字段

- `agent_memory.py` — 会话记忆管理
  - **线程安全**: `threading.RLock` 保护 `_sessions` 并发访问
  - 事实提取 (关键词规则, 不调 LLM)
  - 里程碑摘要 (每 10 轮 LLM 重写)
  - 偏好检测 (LLM 判断新增/取消/删除)
  - Fact 压缩 (LLM 摘要, 超过 40 条时触发)
  - 空闲淘汰 (idle_ttl=1800s) + LRU 上限淘汰 (max_sessions=1000)
  - 公共 API: `has_session()`, `restore_session()` — 消除外部直接访问 `_sessions`

- `redis_store.py` — Redis 只读封装
  - 写入由 Java 负责, Python 仅读取 history 和 memory

### agents/ — Agent 实现

- `base_agent.py` — Agent 基类 (name, capability, execute 计时)
- `coordinator_agent.py` — 任务路由, LLM 三元分类
- `knowledge_agent.py` — 知识检索, LangChain Agent + Tool Calling
- `analysis_agent.py` — 数据分析, LangChain Agent + Tool Calling
- `critic_agent.py` — 答案审核, LLM 输出 verdict
  - LLM 失败时返回 score=0 + need_retry=True（不静默放行）

### generator/ — 答案生成

- `answer_generator.py` — 构建 prompt → LLM 生成 → set_answer()
  - 失败时降级返回 evidence 摘要

### models/ — 数据模型

- `capability.py` — `AgentCapability` (name, description, tools, writes_to, requires)
- `task_graph.py` — `TaskNode` + `TaskGraph` (DAG 任务图)
- `data_types.py` — `Evidence`, `Calculation`, `AnalysisResult`, `CriticResult`, `AgentTrace`
- `schemas.py` — Pydantic 请求/响应模型

### config.py — 配置管理

从 `.env` 加载: LLM_API_KEY, LLM_BASE_URL, LLM_MODEL_NAME, EMBEDDING_MODEL_NAME, OLLAMA_BASE_URL, VECTOR_STORE_PATH, Redis, RabbitMQ, MinIO 等。

CORS origins 从环境变量 `CORS_ORIGINS` 读取，默认 `http://localhost:8080`。

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
# 激活虚拟环境
.venv\Scripts\activate

# 启动主服务 (开发模式, 热重载)
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload

# 启动 RabbitMQ 消费者 (可选, 异步文档向量化)
python -m app.stream_consumer
```

### Docker

```bash
docker compose up -d          # 全部
docker compose up python-ai   # 仅 Python
docker compose logs -f python-ai
```

### 测试

```bash
# 运行全部 85 个单元测试
python -m pytest tests/ -v

# 运行指定测试
python -m pytest tests/test_agent_context.py -v
python -m pytest tests/test_agent_registry.py -v
python -m pytest tests/test_extract_json.py -v
python -m pytest tests/test_rag_engine_parsers.py -v
python -m pytest tests/test_agent_memory_unit.py -v
```

### 环境变量配置 (.env)

```env
LLM_API_KEY=your-api-key-here
LLM_BASE_URL=https://api.deepseek.com
LLM_MODEL_NAME=deepseek-chat
EMBEDDING_MODEL_NAME=nomic-embed-text
EMBEDDING_DEVICE=cpu
OLLAMA_BASE_URL=http://localhost:11434
VECTOR_STORE_PATH=./chroma_db
HOST=0.0.0.0
PORT=8000
REDIS_HOST=localhost
REDIS_PORT=6379
RABBITMQ_HOST=localhost
MINIO_ENDPOINT=http://localhost:9000
CORS_ORIGINS=http://localhost:8080
```

### 代码规范

- Python 3.10+, FastAPI 框架
- 类型注解 + Pydantic 模型校验
- LangChain 0.3+ 生态 (langchain, langchain-chroma, langchain-openai, langchain-ollama)
- MCP 协议 (mcp>=1.0)
- 异步 API (FastAPI async)
- JSON 结构化日志

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
| Agent 能力声明 | `models/capability.py` | AgentCapability dataclass |
| DAG 校验逻辑 | `core/agent_registry.py` | validate_dag() |
| 记忆淘汰策略 | `core/agent_memory.py` | idle_ttl, max_sessions, REWRITE_INTERVAL |
| JSON 日志格式 | `core/log_config.py` | JSONFormatter |
| CORS 配置 | `.env` | CORS_ORIGINS 环境变量 |

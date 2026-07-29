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
├── pyproject.toml               # 项目依赖配置
├── .env                         # 环境变量 (API Key, 模型配置)
├── .env.example                 # 环境变量示例
├── chroma_db/                   # Chroma 向量数据库持久化存储
├── ai_service.log               # 服务日志
└── app/
    ├── main.py                  # FastAPI 入口 (初始化所有组件)
    ├── config.py                # 配置管理 (读取 .env)
    ├── prompt_manager.py        # 提示词管理器 (从 prompts.yaml 加载)
    ├── prompts.yaml             # 所有提示词模板
    ├── exceptions.py            # 业务异常定义
    ├── stream_consumer.py       # RabbitMQ 消费者 (异步文档向量化)
    ├── mcp_client.py            # MCP Client (连接 MCP Server)
    ├── mcp_server.py            # MCP Server (工具执行进程, stdio 模式)
    ├── mcp_tools.py             # LangChain 工具定义 (封装 MCP 调用)
    ├── api/
    │   ├── ingestion.py         # 文档向量化 API
    │   └── qa.py                # 问答 API (Multi-Agent 入口)
    ├── core/
    │   ├── document_processor.py # 文档解析/切片
    │   ├── vector_store.py      # 向量数据库封装
    │   ├── rag_engine.py        # RAG 核心引擎 (搜索/计算逻辑)
    │   ├── agent_context.py     # Agent 间共享上下文
    │   ├── agent_orchestrator.py # Agent 编排器 (调度所有 Agent)
    │   ├── agent_memory.py      # 会话记忆管理 (事实/偏好/里程碑)
    │   ├── redis_store.py       # Redis 读取封装 (只读, 写入由 Java 负责)
    │   └── agents/
    │       ├── base_agent.py    # Agent 基类
    │       ├── coordinator_agent.py  # 任务路由 (LLM 分类)
    │       ├── knowledge_agent.py    # 知识检索 (Tool Calling)
    │       ├── analysis_agent.py     # 数据分析 (Tool Calling)
    │       └── critic_agent.py       # 答案审核
    └── models/
        └── schemas.py           # Pydantic 数据模型
```

---

## Multi-Agent 架构

### 请求执行流程

```
用户提问
  │
  ▼
qa.py → Orchestrator.run(context: AgentContext)
  │
  ├─ 1. 恢复记忆 (Redis → AgentMemory)
  │
  ├─ 2. Coordinator Agent ── LLM 分类
  │     输出: {needs_plan, needs_analysis, needs_review}
  │
  ├─ 3. 判断路径:
  │     ├─ 简单模式: Knowledge → Analysis(可选) → Critic(可选)
  │     └─ 规划模式: Plan → 逐步执行 → Replan → Critic(可选)
  │
  ├─ 4. Critic Agent ── LLM 审核 (最多重试 2 次)
  │
  └─ 5. 更新记忆 (事实/偏好/里程碑)
```

### Agent 清单

| Agent | 文件 | 职责 | LLM 调用方式 |
|-------|------|------|-------------|
| Coordinator | `agents/coordinator_agent.py` | 问题分类 (needs_plan/needs_analysis/needs_review) | 单次调用, 输出 JSON |
| Knowledge | `agents/knowledge_agent.py` | 知识检索 + 简单问题回答 | Tool Calling 循环 (搜索最多 2 次) |
| Analysis | `agents/analysis_agent.py` | 数值计算 (求和/排名) | Tool Calling 循环 (计算最多 8 次) |
| Critic | `agents/critic_agent.py` | 答案质量审核 | 单次调用, 输出 verdict JSON |

### 上下文传递机制

**AgentContext** (`agent_context.py`) — Agent 间共享:
```
输入字段: question, session_id, document_ids, history, preferences
Knowledge 写入: knowledge_chunks, knowledge_filtered, sources, tools_called
Analysis 写入: answer, is_agg
Critic 写入: critique, reflection_count
Orchestrator 写入: plan
执行轨迹: steps[]
```

**MCPState** (`mcp_server.py`) — 工具间共享:
- `search_documents` 执行后写入 `MCPState.search_ctx` (SearchContext)
- `calculate_sum` / `calculate_rank` / `read_all_rows` 从 `MCPState.search_ctx` 读取搜索结果

### LLM 调用统计

| 调用位置 | 次数 | 作用 |
|---------|------|------|
| Coordinator 分类 | 1 次 (必调) | 判断 needs_plan / needs_analysis / needs_review |
| Knowledge Agent tool calling | 2-4 次 | 决定搜索词 → 调工具 → 判断结果 → 生成回答 |
| Analysis Agent tool calling | 1-3 次 | 决定计算方式 → 调工具 → 生成回答 |
| Plan-and-Execute | 0-5+ 次 | 生成计划 / 调整计划 / 最终生成 |
| Critic 审核 | 0-3 次 | 判断答案质量, 不通过则重试 |
| Agent Memory 偏好检测 | 0-1 次 | 每轮检测偏好变化 (有已有偏好时) |
| Agent Memory 摘要重写 | 0-1 次 | 每 10 轮或累积 3+ 事实时触发 |
| **最简问答** | **~2 次** | Coordinator + Knowledge 直接回答 |
| **简单搜索** | **~4-5 次** | Coordinator + Knowledge(2-3) + Memory(1) |
| **需计算** | **~6-8 次** | Coordinator + Knowledge(2-3) + Analysis(1-3) + Memory(1) |
| **复杂多步+审核** | **10+ 次** | 全流程 + Replan + Critic 重试 |

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

- `mcp_client.py` — Client 端, 通过 stdio 与 Server 通信
- `mcp_server.py` — Server 端, 注册工具并执行
- `mcp_tools.py` — 将 MCP 工具封装为 LangChain tools

---

## 模块职责

### api/ - 接口层
- `ingestion.py`:
  - `POST /ingest/document` - 接收文档内容, 解析、切片、向量化存入 Chroma
  - `DELETE /ingest/document/{id}` - 删除指定文档的向量
- `qa.py`:
  - `POST /qa/ask` - Multi-Agent 问答入口, 接收问题返回答案

### core/ - 核心业务层
- `document_processor.py` - 文档处理器
  - 支持格式: PDF、DOCX、TXT、MD、XLSX
  - Excel 每行视为独立 chunk, 不做文本切片
  - `RecursiveCharacterTextSplitter` 切片 (1000字符/200重叠, 非 Excel 文档)
  - 中文分隔符优先: `\n\n`, `\n`, `。`, `；`, `，`
- `vector_store.py` - 向量数据库封装
  - Chroma + Ollama Embeddings (nomic-embed-text)
  - 提供: add_texts, similarity_search, keyword_search, delete_document
- `rag_engine.py` - RAG 核心引擎
  - 搜索: Embedding 相似度 + 关键词补充 + 文件名回退
  - 余弦距离阈值 0.92, 过滤低分结果
  - 轮询选取: 确保各文档均匀参与
  - 假阳性过滤: 关键词包含检查 (纯字符串匹配, 不调 LLM)
  - 计算工具: `_sum_by_key`, `_rank_by_key` (纯算法, 不调 LLM)
- `agent_orchestrator.py` - Agent 编排器
  - 调度所有 Agent 的执行顺序
  - 管理 Plan-and-Execute 流程
  - 处理 Critic 审核失败重试 (最多 2 次)
- `agent_context.py` - Agent 间共享上下文 dataclass
- `agent_memory.py` - 会话记忆管理
  - 事实提取 (关键词规则, 不调 LLM)
  - 里程碑摘要 (每 10 轮 LLM 重写)
  - 偏好检测 (LLM 判断新增/取消)
  - Fact 压缩 (LLM 摘要, 超过 40 条时触发)

### agents/ - Agent 实现
- `base_agent.py` - Agent 基类 (name, execute 计时)
- `coordinator_agent.py` - 任务路由, LLM 三元分类
- `knowledge_agent.py` - 知识检索, LangChain Agent + Tool Calling
- `analysis_agent.py` - 数据分析, LangChain Agent + Tool Calling
- `critic_agent.py` - 答案审核, LLM 输出 verdict

### models/ - 数据模型
- `schemas.py` - Pydantic 请求/响应模型

### config.py - 配置管理
从 `.env` 加载: LLM_API_KEY, LLM_BASE_URL, LLM_MODEL_NAME, EMBEDDING_MODEL_NAME, OLLAMA_BASE_URL, VECTOR_STORE_PATH, Redis, RabbitMQ, MinIO 等。

### prompts.yaml - 提示词模板
- `tool_calling.system` — Knowledge/Analysis Agent 的通用系统提示
- `critic.evaluate` — Critic Agent 审核提示
- `planner.system` — 规划器提示
- `replanner.system` — 计划调整器提示

---

## 外部依赖服务

- **Ollama**: localhost:11434 (本地 Embedding 模型: nomic-embed-text)
- **DeepSeek API**: api.deepseek.com (LLM 推理)
- **ChromaDB**: 本地持久化存储 (`./chroma_db/`)
- **Redis**: localhost:6379 (对话历史 + 记忆持久化, Python 只读, Java 写入)
- **RabbitMQ**: localhost:5672 (文档向量化异步任务队列)
- **MinIO**: localhost:9000 (文档文件存储)

Java 后端通过 `http://localhost:8000` 调用本服务的 API。

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

**API 文档:** http://localhost:8000/docs (Swagger UI)

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
```

### 依赖管理
```bash
# 项目使用 pyproject.toml 管理依赖
pip install -e .
```

### 代码规范
- Python 3.10+, FastAPI 框架
- 类型注解 + Pydantic 模型校验
- LangChain 0.3+ 生态 (langchain, langchain-chroma, langchain-openai, langchain-ollama)
- MCP 协议 (mcp>=1.0)
- 异步 API (FastAPI async)
- 日志输出到 `ai_service.log`

### 修改注意事项
- 文档切片参数修改在 `core/document_processor.py` (chunk_size, chunk_overlap)
- RAG 检索阈值修改在 `core/rag_engine.py` (SCORE_THRESHOLD = 0.92)
- Embedding 模型切换需修改 `.env` 中的 `EMBEDDING_MODEL_NAME` 和 `OLLAMA_BASE_URL`
- LLM 模型切换需修改 `.env` 中的 `LLM_MODEL_NAME` 和 `LLM_BASE_URL`
- 修改 API 路径需同步更新 Java 后端 `QaServiceImpl` 和 `DocumentServiceImpl` 中的 HTTP 调用
- 修改 Agent 行为: `core/agents/` 下对应 Agent + `prompts.yaml` 中的提示词
- 新增 MCP 工具: 在 `mcp_server.py` 注册 + 在 `mcp_tools.py` 封装为 LangChain tool
- Critic 审核逻辑修改在 `core/agents/critic_agent.py`
- 规划/重规划逻辑修改在 `core/agent_orchestrator.py` (_plan/_replan)
- 记忆管理修改在 `core/agent_memory.py` (REWRITE_INTERVAL, FACT_HARD_LIMIT 等常量)

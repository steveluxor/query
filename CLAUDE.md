# Query - RAG 智能知识库问答系统 (Python AI 服务)

## 系统架构

本项目是一个 **RAG (Retrieval-Augmented Generation) 智能知识库问答系统**，采用 **Multi-Agent 架构**，通过 MCP (Model Context Protocol) 协议实现工具调用。

| 组件 | 路径 | 技术栈 | 端口 |
|------|------|--------|------|
| 前端 | `D:\DOWNLOAD\nginx-query` | Nginx + 原生 HTML/CSS/JS | :8080 |
| Java 后端 | `D:\IntelliJ IDEA 2025.1.3\project\Query` | Spring Boot 4.0.6 + MyBatis | :8085 |
| Python AI 服务 | 本项目 (`D:\DOWNLOAD\pycharm\query`) | FastAPI + LangChain + ChromaDB | :8000 |

**请求流向：** 前端(:8080) → Nginx 反向代理 → Java 后端(:8085) → Python AI 服务(:8000)

---

## Multi-Agent 信息流

```
Question → Coordinator → Knowledge → Analysis(可选) → Generate → Critic(可选) → Answer
```

**简单模式**: Knowledge → Analysis(可选) → Generate → Critic(可选)
**规划模式**: Planner 拆多步 → 逐步执行(Knowledge/Analysis) → Evidence 累积 → Generate → Critic

Critic 重试机制: `retry_target` 控制重跑范围 (knowledge/analysis/generator/all)，最多 2 次。

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
    ├── exceptions.py            # 自定义异常
    ├── stream_consumer.py       # RabbitMQ 消费者 (Docker 独立容器)
    ├── api/
    │   ├── ingestion.py         # 文档向量化 API
    │   └── qa.py                # 问答 API
    ├── core/
    │   ├── agent_context.py     # Agent 上下文
    │   ├── agent_memory.py      # 记忆系统
    │   ├── agent_orchestrator.py # Agent 编排器
    │   ├── document_processor.py # 文档解析/切片
    │   ├── rag_engine.py        # RAG 引擎
    │   ├── redis_store.py       # Redis 存储
    │   ├── vector_store.py      # 向量数据库
    │   ├── prompt_manager.py    # 提示词管理
    │   ├── prompts.yaml         # 提示词模板
    │   ├── mcp/                 # MCP 协议层
    │   │   ├── client.py        # MCP Client
    │   │   ├── server.py        # MCP Server
    │   │   └── tools.py         # LangChain tools 包装
    │   ├── agents/
    │   │   ├── base_agent.py    # Agent 基类
    │   │   ├── coordinator_agent.py # 任务路由
    │   │   ├── knowledge_agent.py   # 知识检索
    │   │   ├── analysis_agent.py    # 数据分析
    │   │   └── critic_agent.py      # 答案审核
    │   └── generator/
    │       └── answer_generator.py  # 答案生成
    └── models/
        ├── data_types.py        # 数据类
        └── schemas.py           # Pydantic 模型
```

---

## 文件与函数详解

### `app/main.py` — FastAPI 入口

| 函数 | 说明 |
|------|------|
| `lifespan(app)` | async context manager，启动时初始化 VectorStore、RAGEngine、AgentMemory、RedisStore、MCPClient、AgentOrchestrator；关闭时释放资源 |
| `health()` | GET /health，返回 `{"status": "ok"}` |
| `biz_exception_handler(request, exc)` | 捕获 BizException → 400 JSON |
| `validation_exception_handler(request, exc)` | 捕获 RequestValidationError → 422 JSON |
| `global_exception_handler(request, exc)` | 兜底异常 → 500 JSON |

启动顺序: VectorStore → RAGEngine → AgentMemory → RedisStore → MCPClient.connect() → AgentOrchestrator

### `app/config.py` — 配置管理

`Settings(BaseSettings)` 从 `.env` 加载所有配置，字段：

| 分类 | 字段 | 默认值 |
|------|------|--------|
| LLM | `llm_api_key`, `llm_base_url`, `llm_model_name` | deepseek-chat |
| Embedding | `embedding_model_name`, `embedding_device` | nomic-embed-text / cpu |
| Ollama | `ollama_base_url` | localhost:11434 |
| VectorStore | `vector_store_path` | ./chroma_db |
| Server | `host`, `port` | 0.0.0.0:8000 |
| RabbitMQ | `rabbitmq_host/port/user/password/vhost/ingest_*` | localhost:5672 |
| MinIO | `minio_endpoint/access_key/secret_key/bucket` | localhost:9000 |
| Redis | `redis_host/port/password/db/history_key_prefix/memory_key_prefix` | localhost:6379 |
| 内部 | `python_base_url`, `ingest_path`, `java_base_url` | localhost:8000 / :8085 |

### `app/exceptions.py` — 自定义异常

| 类 | 说明 |
|----|------|
| `BizException(code, message)` | 业务异常，被 main.py 的异常处理器捕获 |
| `ErrorCode` | 常量: PARAM_ERROR=400, NOT_FOUND=404, SERVER_ERROR=500, VECTOR_STORE_ERROR=1001, LLM_ERROR=1002, DOCUMENT_PARSE_ERROR=1003 |

### `app/stream_consumer.py` — RabbitMQ 消费者

Docker 独立容器运行，`python -m app.stream_consumer`。

| 函数 | 说明 |
|------|------|
| `_get_minio_client()` | MinIO 客户端单例 |
| `download_from_minio(file_path, local_path)` | 从 MinIO 下载文件到本地 |
| `update_document_status(document_id, status)` | 调 Java PUT API 更新文档状态 |
| `process_message(ch, method, properties, body)` | RabbitMQ 回调: 解析消息 → MinIO 下载 → 调 /ingest/document 向量化 → 更新状态 → ack/nack |
| `main()` | 启动消费者，声明 exchange/queue/binding，带断线重连 (5s) |

消息格式: `{"documentId": int, "filePath": str, "fileName": str}`

### `app/api/ingestion.py` — 文档向量化 API

| 函数/路由 | 说明 |
|-----------|------|
| `get_vector_store(request)` | FastAPI 依赖注入，从 app.state 获取 VectorStore |
| `_get_minio_client()` | MinIO 客户端单例 |
| `POST /ingest/document` → `ingest_document(request, vector_store)` | 接收 IngestRequest，从 MinIO 下载（如需）→ DocumentProcessor.process() 切片 → 删除旧向量 → add_texts 存入 Chroma → 返回 IngestResponse |
| `DELETE /ingest/document/{id}` → `delete_document(document_id, vector_store)` | 删除指定文档的所有向量 |

### `app/api/qa.py` — 问答 API

| 函数/路由 | 说明 |
|-----------|------|
| `get_agent_memory(request)` | 依赖注入 AgentMemory |
| `get_orchestrator(request)` | 依赖注入 AgentOrchestrator |
| `POST /qa/ask` → `ask_question(request, orchestrator, agent_memory)` | 构造 AgentContext → orchestrator.run() → 提取 memory_data → 返回 MultiAgentResponse |

返回字段: answer, sources, is_agg, tools_called, session_id, memory_data, reflection_count, plan, agent_trace

### `app/core/agent_context.py` — Agent 上下文

**AgentStep** dataclass: `name`, `duration_ms`, `summary`

**AgentContext** dataclass — Agent 间共享上下文，字段所有权保护：

| 方法 | 说明 |
|------|------|
| `set_evidence(evidence)` | 写保护: evidence 非空时抛 RuntimeError |
| `set_sources(sources)` | 写保护: sources 非空时抛 RuntimeError |
| `set_analysis(analysis)` | 写保护: analysis 非 None 时抛 RuntimeError |
| `set_answer(answer)` | 设置最终回答 |
| `set_critique(critique, need_retry, retry_target)` | 设置审核结果 |
| `add_trace(trace)` | 追加 AgentTrace |
| `reset_for_retry(target)` | 根据 retry_target 清空对应字段，允许重新写入 |

字段分组: 输入字段(question, session_id, document_ids, history, memory_context, top_k, preferences) → Coordinator 写入(plan) → Knowledge 写入(evidence, sources) → Analysis 写入(analysis) → AnswerGenerator 写入(answer) → Critic 写入(critique, need_retry, retry_target) → 执行轨迹(traces, steps)

### `app/core/agent_orchestrator.py` — Agent 编排器

调度链: Coordinator → Knowledge → Analysis(可选) → Generate → Critic(可选)

| 方法 | 说明 |
|------|------|
| `__init__(rag_engine, agent_memory, redis_store, mcp_client)` | 创建 Coordinator, KnowledgeAgent, AnalysisAgent, CriticAgent, AnswerGenerator |
| `run(context)` | 主流程: 恢复记忆 → 设置文档权限 → 偏好检测∥分类 → 规划/简单模式 → Critic → 更新记忆 |
| `_run_critic_with_retry(context)` | Critic 审核循环 (最多 MAX_CRITIC_RETRIES=2)，根据 retry_target 重跑对应 Agent |
| `_execute_plan(context, plan)` | 规划模式: 逐步执行，每步保存/恢复 question，Evidence 跨步骤累积 |
| `_parse_json(text)` | 解析 LLM 输出的 JSON (兼容 markdown 代码块) |
| `_plan(question, memory_context)` | 调用 Planner LLM 生成步骤列表，prompt 来自 prompts.yaml |
| `_normalize_step(step)` | 标准化步骤为 `{"agent": "knowledge"/"analysis", "query": ...}` |
| `_restore_memory(context)` | 从 Redis 恢复 history + memory，调 rebuild_from_history 重建 |
| `_update_memory(context)` | 调 agent_memory.update() 更新当前轮记忆 |

### `app/core/agents/base_agent.py` — Agent 基类

| 方法 | 说明 |
|------|------|
| `execute(context)` | 包装 run(): 计时 → 调 run() → 记录 AgentStep → 异常处理 |
| `run(context)` | 抽象方法，子类实现 |

### `app/core/agents/coordinator_agent.py` — 任务路由

| 方法 | 说明 |
|------|------|
| `run(context)` | 调用 _classify()，设置 self.needs_plan/needs_analysis/needs_review |
| `_classify(question)` | LLM 分类 (CLASSIFY_SYSTEM prompt)，返回 `{needs_plan, needs_analysis, needs_review}` JSON |

LLM 配置: temperature=0, max_tokens=50, timeout=30

### `app/core/agents/knowledge_agent.py` — 知识检索

工具: search_documents, list_documents, read_all_rows (通过 MCP)

| 方法 | 说明 |
|------|------|
| `run(context)` | 创建 LangChain Agent → 执行 → 解析 Evidence → 提取 sources |
| `_extract_tool_results(messages)` | 从消息列表提取所有 tool 类型消息的 content |
| `_parse_evidence(messages)` | 从最后一条非 tool_call 的 AI 消息解析 Evidence JSON |
| `_extract_evidence_from_text(text)` | JSON 解析: 直接解析 → markdown 代码块 → {} 块提取 |
| `_extract_sources(tool_results, evidence)` | 从 evidence 中提取去重的 file_name + statement 前 200 字 |

搜索限制: 最多 2 次 search_documents (RAG Engine 层面限制)

### `app/core/agents/analysis_agent.py` — 数据分析

工具: calculate_sum, calculate_rank (通过 MCP)

| 方法 | 说明 |
|------|------|
| `run(context)` | 创建 LangChain Agent → 执行 → 解析 AnalysisResult |
| `_parse_analysis(messages)` | 从最后一条 AI 消息解析 AnalysisResult JSON |
| `_extract_analysis_from_text(text)` | JSON 解析 (兼容 markdown 代码块) |

计算限制: 最多 8 次工具调用 (RAG Engine 层面限制)

### `app/core/agents/critic_agent.py` — 答案审核

| 方法 | 说明 |
|------|------|
| `run(context)` | 构建 prompt → LLM 评估 → set_critique() → 记录 AgentTrace |
| `_build_prompt(context)` | 格式化 evidence/analysis/answer 为 CRITIC_PROMPT |
| `_parse_result(text)` | 解析 CriticResult JSON: 直接解析 → markdown 代码块 → {} 块提取 |

评估维度: 准确性、完整性、来源引用、逻辑一致性。score 1-10，retry_target: knowledge/analysis/generator/all

### `app/core/generator/answer_generator.py` — 答案生成

| 方法 | 说明 |
|------|------|
| `generate(context)` | 构建 prompt → LLM 生成 → set_answer()；失败时降级 |
| `_build_prompt(context)` | 组装 question + evidence(带 source/type) + analysis(calculations/findings/conclusions) + sources |
| `_fallback_answer(context)` | LLM 失败时返回 evidence 前 5 条摘要，或"服务不可用" |

LLM 配置: temperature=0.1, max_tokens=4096

### `app/core/agent_memory.py` — 记忆系统

进程内记忆管理，纯内存无持久化。Java MySQL 是 source of truth，重启后通过 rebuild_from_history 重建。

**常量**: REWRITE_INTERVAL=10 (LLM 重写间隔), FACT_HARD_LIMIT=50, FACT_PRUNE_TRIGGER=40, FACT_KEEP_RECENT=15

| 方法 | 说明 |
|------|------|
| `get_or_create(session_id)` | 懒加载: 不存在则创建 SessionMemory，触发空闲淘汰 |
| `rebuild_from_history(session_id, history, preferences)` | 幂等重建: 仅首次生效，逐轮 _apply_turn()，用 Java 传来的 preferences 覆盖 |
| `to_dict(session_id)` | 序列化为 dict (供 response 返回 → Java 写 Redis)，仅 dirty 时返回 |
| `from_dict(data, session_id)` | 从 dict 恢复 (从 Redis 加载)，兼容旧数据 |
| `update(session_id, turn)` | 里程碑压缩 + 事实提取 (去重) + 文档事实 + fact 裁剪 |
| `update_preferences(session_id, question)` | 独立偏好检测，供 qa.py 与主 LLM 并行调用 |
| `format_context(session_id)` | 格式化为文本块: [对话里程碑] + [已知事实] (最近20条) + [用户偏好] |
| `_apply_turn(memory, turn)` | 内部: 单轮应用到 memory (重建用) |
| `_evict_if_needed()` | 空闲淘汰 (idle_ttl=1800s) + LRU 上限淘汰 (max_sessions=1000) |
| `_compress_summary(memory, question, answer)` | 每 10 轮或累积 3+ 新事实时 LLM 重写里程碑 |
| `_rewrite_summary_and_extract(...)` | 一次 LLM 调用: 摘要 + 偏好提取/删除 + 事实提取 |
| `_compress_old_facts(memory)` | facts 超 40 条时 LLM 压缩旧条目为摘要 |
| `_extract_facts(question, was_agg)` | 基于关键词规则提取事实 (求和/排序/筛选/对比/趋势等) |
| `_check_preference_changes(memory, question)` | LLM 判断偏好变化 (新增/修改/删除)，每轮调用 |
| `_extract_doc_facts(turn)` | 从 document_names/document_ids 提取文档事实 |

### `app/core/redis_store.py` — Redis 存储

只读封装，写入由 Java 负责。

| 方法 | 说明 |
|------|------|
| `__init__()` | 创建 aioredis 客户端 |
| `get_recent_history(session_id, limit=10)` | LRANGE 读取最近 N 轮对话 |
| `get_memory(session_id)` | GET + from_dict 恢复 AgentMemory |
| `safe_get_memory(session_id)` | 安全版本: Redis 异常返回 None |
| `safe_get_history(session_id, limit=10)` | 安全版本: Redis 异常返回空列表 |
| `close()` | 关闭连接 |

Key 格式: `qa:history:{session_id}`, `qa:memory:{session_id}`

### `app/core/vector_store.py` — 向量数据库

Chroma + Ollama Embeddings 封装。

| 方法 | 说明 |
|------|------|
| `__init__()` | 初始化 OllamaEmbeddings + Chroma (collection="rag_docs") |
| `add_texts(texts, metadatas)` | 向量化并存入 Chroma |
| `similarity_search(query, k=5, filter=None)` | 语义检索，返回 [(Document, score)] |
| `delete_by_document_id(document_id)` | 删除文档向量，累计删除 50 次触发 compact |
| `compact()` | 重建索引: get all → reset → re-add，释放 tombstone 空间 |
| `get_all_chunks(filter=None)` | 不走相似度搜索，返回全部 chunk (用于聚合查询) |
| `keyword_search(keywords, filter, max_results=20)` | 关键词精确匹配，评分 = 匹配数/总关键词数 |
| `get_document_names()` | 返回 {document_id: file_name} 映射 |
| `close()` | 释放 Chroma 底层资源 |

### `app/core/rag_engine.py` — RAG 引擎

**常量**: SCORE_THRESHOLD=0.92 (余弦距离), DEFAULT_TOP_K=10, MIN_GAP_THRESHOLD=0.05

**SearchContext** / **AnalysisContext**: 工具间共享状态 (chunks, filtered, all_chunks, sources, search_count, agg_count, document_ids, tools_called)

| 方法 | 说明 |
|------|------|
| `__init__(vector_store)` | 初始化 ChatOpenAI LLM |
| `_ctx_chunks/filtered/all_chunks(ctx)` | 上下文兼容: 从 SearchContext 或 AnalysisContext 取字段 |
| `_create_search_tools(ctx)` | 创建 search_documents + list_documents LangChain tools |
| `_create_analysis_tools(ctx)` | 创建 calculate_sum + calculate_rank + read_all_rows LangChain tools |
| `_determine_top_k(filtered)` | 根据分数间隔动态决定 top_k |
| `_select_by_diversity(filtered, top_k)` | 轮询各文档，保证多样性 |
| `_bigrams(text)` | 生成二元分词集合 |
| `_extract_chinese(text)` | 提取中文字符集合 |
| `_extract_keywords(text)` | jieba 分词，过滤停用词和单字 |
| `_filename_fallback(question, document_ids)` | 文件名 bigram 匹配 → 单字重叠降级 → 获取匹配文档全部 chunk |
| `_parse_row_filter(row_filter_str)` | 解析 "前10行"→(le,10), "第5行之后"→(ge,5) 等 |
| `_parse_content_filter(content_filter)` | 解析 "品牌=万代"→("品牌","万代") |
| `_filter_chunks_by_content(chunks, key, value)` | 按 "key: value" 行过滤 chunk |
| `_is_empty_record(content)` | 检查标识类字段是否全为空值 |
| `_rank_by_key(chunks, key_name)` | 提取指定 key 数值并排序，找不到 key 时自动回退到数值最多的 key |
| `_sum_by_key(chunks, key_name, row_filter)` | 提取指定 key 数值并求和，支持行号过滤，找不到 key 时自动回退 |
| `_execute_search(query, row_start, row_end, ctx)` | 搜索主流程: embedding(k=60) → 阈值过滤 → 关键词补充 → 合并 → 多样性选取 → 文件名回退 → 格式化输出 |
| `_execute_sum(key_name, row_filter, content_filter, ctx)` | 执行求和，返回格式化计算结果 (含公式和详细数据) |
| `_execute_rank(key_name, ascending, position, content_filter, ctx)` | 执行排名，返回格式化排名结果 |
| `_load_all_chunks(ctx)` | 惰性加载: 从搜索到的文档 ID 加载全部 chunk，过滤汇总行 |
| `_execute_read_all_rows(ctx)` | 返回已搜索文档的完整数据行 |

搜索流程: embedding 相似度(60) → 阈值过滤(0.92) → 关键词搜索补充 → 合并 → 多样性选取 → 文件名回退补充

### `app/core/document_processor.py` — 文档处理

| 类/方法 | 说明 |
|---------|------|
| `WordDocLoader(file_path).load()` | Windows COM 解析旧版 .doc 文件 |
| `ExcelLoader(file_path).load()` | openpyxl 解析 xlsx，每行 → Document (格式: "行号: N\n列名: 值")，跳过空行和汇总行 |
| `DocumentProcessor(chunk_size=1000, chunk_overlap=200)` | 初始化 RecursiveCharacterTextSplitter |
| `DocumentProcessor.process(file_path, document_id, file_name)` | 按扩展名选择 Loader → 切片 (Excel 跳过) → 添加 "[文件: name]" 前缀 → 返回 [{text, metadata}] |

支持格式: .pdf (PyPDFLoader), .docx (Docx2txtLoader), .doc (WordDocLoader/COM), .txt/.md (TextLoader), .xlsx (ExcelLoader)

### `app/core/prompt_manager.py` — 提示词管理

| 方法 | 说明 |
|------|------|
| `PromptManager.initialize(path)` | 加载 prompts.yaml (懒加载，默认路径: 同目录下) |
| `PromptManager.get(*keys)` | 按 key 链获取: get("knowledge", "system") → prompts["knowledge"]["system"] |

### `app/core/mcp/client.py` — MCP Client

通过 stdio 连接 MCP Server。

| 方法 | 说明 |
|------|------|
| `__init__(server_command, server_args, env)` | 配置 StdioServerParameters，继承当前进程环境变量 |
| `connect()` | AsyncExitStack → stdio_client → ClientSession → initialize |
| `disconnect()` | aclose() 释放连接 |
| `call_tool(tool_name, arguments)` | 调用 MCP 工具，返回 result.content[0].text |
| `list_tools()` | 获取 MCP Server 工具列表 |

### `app/core/mcp/server.py` — MCP Server

独立进程运行，持有 RAGEngine 实例。

**MCPState**: 工具间共享状态 (search_ctx: SearchContext, document_ids: list[int])

| 工具 | 参数 | 说明 |
|------|------|------|
| `set_document_ids` | `ids: list[int]` | 设置有权限的文档 ID |
| `search_documents` | `query, row_start?, row_end?` | 搜索文档，返回 JSON (rows_returned, is_complete, available_actions, data) |
| `list_documents` | 无 | 列出可检索的文档 |
| `calculate_sum` | `key_name, row_filter?, content_filter?` | 求和 |
| `calculate_rank` | `key_name, ascending, position?, content_filter?` | 排名 |
| `read_all_rows` | 无 | 读取完整数据行 |
| `main()` | — | 初始化 VectorStore + RAGEngine，启动 stdio 模式 |

### `app/core/mcp/tools.py` — LangChain Tools 包装

| 函数 | 说明 |
|------|------|
| `create_mcp_tools(mcp_client, include=None)` | 将 MCP 工具包装为 LangChain tools (search_documents, list_documents, calculate_sum, calculate_rank, read_all_rows)。`include` 参数筛选指定工具 |

### `app/models/data_types.py` — 数据类

| dataclass | 字段 | 说明 |
|-----------|------|------|
| `Evidence` | statement, source, evidence_type, metadata | Knowledge Agent 输出的事实证据 |
| `Calculation` | operation, field, arguments, result, source | Analysis Agent 的单次计算记录 |
| `AnalysisResult` | calculations, findings, conclusions | Analysis Agent 结构化输出 |
| `CriticResult` | score(1-10), problems, need_retry, retry_target | Critic Agent 审核结果 |
| `AgentTrace` | agent, start_time, end_time, tools_called, input_summary, output_summary | 执行轨迹 |

### `app/models/schemas.py` — Pydantic 模型

| 模型 | 用途 |
|------|------|
| `IngestRequest` | file_path, document_id, file_name |
| `IngestResponse` | document_id, status |
| `HistoryItem` | question, answer, is_agg |
| `QuestionRequest` | question, document_ids, top_k, history, session_id, strategy, preferences |
| `Source` | document_id, file_name, content, score |
| `AnswerResponse` | 旧版响应 (answer, sources, is_agg, tools_called, session_id, memory_data, reflection_count, plan) |
| `AgentStepInfo` | name, duration_ms, summary |
| `MultiAgentResponse` | 同 AnswerResponse + agent_trace |

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

### 启动

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

### 修改注意事项

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

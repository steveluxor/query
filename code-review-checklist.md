# Python AI 服务 — 代码复盘清单

> 系统已从 RAG 项目升级为 Agent 平台，复盘重点：Orchestrator + Registry + Context + TaskGraph

---

## 第一遍：建立运行模型

### 1. 配置与入口

| 顺序 | 文件 | 看点 |
|------|------|------|
| 1 | `app/config.py` | LLM / Embedding / Redis / MCP / 外部服务依赖 |
| 2 | `app/main.py` | 启动顺序：VectorStore → RAGEngine → Memory → MCPClient → Agents → Orchestrator；JSON 结构化日志 |
| 3 | `app/core/llm_factory.py` | `create_llm()` 统一 LLM 创建，消除 4 处重复 |
| 4 | `app/core/log_config.py` | JSONFormatter + setup_logging() |
| 5 | `app/core/prompt_manager.py` | 加载 prompts.yaml，`get(*keys)` 按 key 链获取 |
| 6 | `app/core/prompts.yaml` | 所有 Agent 的 prompt 驱动：planner / critic / knowledge / analysis |

---

## 第二遍：核心数据契约

> 先理解 Agent 之间"说什么话"，再看 Agent 怎么工作

### 2. 数据结构

| 顺序 | 文件 | 看点 |
|------|------|------|
| 7 | `app/models/data_types.py` | Evidence / Calculation / AnalysisResult / CriticResult / AgentTrace — **整个 Multi-Agent 的语言** |
| 8 | `app/models/task_graph.py` | TaskNode（id, agent, objective, depends_on, output_key, status）+ TaskGraph — Planner 生成，Orchestrator 执行 |
| 9 | `app/models/capability.py` | AgentCapability（name, tools, writes_to, requires）— Agent 不是简单类，而是类 + 能力声明 |
| 10 | `app/core/agent_context.py` | **所有 Agent 通信靠它**。重点：`set_evidence()` 写保护 + `reset_for_retry()` 清空机制 |
| 11 | `app/core/utils.py` | `extract_json()` — 统一 LLM 输出 JSON 提取（直接解析 → markdown 代码块 → 深度匹配 {} 提取） |

---

## 第三遍：Agent 核心架构

### 3. 基础设施

| 顺序 | 文件 | 看点 |
|------|------|------|
| 12 | `app/core/agents/base_agent.py` | 所有 Agent 生命周期：`execute()` → `run()` → AgentTrace；`capability` 类属性 |
| 13 | `app/core/agent_registry.py` | **能力描述层**（不是 dict）：register / get_agent / validate_dag / find_by_tool / find_by_writes / format_for_prompt |
| 14 | `app/core/agent_orchestrator.py` | **整个项目核心**，建议分四部分看 ↓ |

### 4. Orchestrator 拆解

| 部分 | 方法 | 看点 |
|------|------|------|
| 14a | `__init__` | `create_default_registry()` + Agent 实例注册到 Registry |
| 14b | `run()` | 主流程 9 步：memory → coordinator → plan? → execute → generator → critic → memory |
| 14c | `_plan()` / `_execute_plan()` / `_validate_task_graph()` | DAG 执行：Registry.validate_dag() + requires 校验 + 拓扑调度 |
| 14d | `_retry_from_task()` / `_run_critic_with_retry()` | **面试最大亮点**：Critic 精确定位 task_id → BFS 下游 → 拓扑重跑 |

---

## 第四遍：Agent 实现

### 5. 各 Agent

| 顺序 | 文件 | 看点 |
|------|------|------|
| 15 | `app/core/agents/coordinator_agent.py` | **不是 Planner**。Coordinator 决定"是否需要复杂流程"，Planner 决定"怎么拆任务" |
| 16 | `app/core/agents/knowledge_agent.py` | Tool calling → raw result → Evidence 提取 → `context.set_evidence()`。`capability` 声明 tools + writes_to |
| 17 | `app/core/agents/analysis_agent.py` | `requires=["evidence"]` → tool 计算 → AnalysisResult |
| 18 | `app/core/generator/answer_generator.py` | 为什么独立：Evidence + Analysis → Generator → Answer（统一出口） |
| 19 | `app/core/agents/critic_agent.py` | 审核 → 定位问题 → retry_target。**失败安全**：LLM 异常时 score=0 + need_retry=True（不静默放行） |

---

## 第五遍：MCP 工具链

> Agent 不知道 RAG，Agent 只知道 Tool

| 顺序 | 文件 | 看点 |
|------|------|------|
| 20 | `app/core/mcp/client.py` | StdioClientTransport → ClientSession，call_tool() / list_tools() |
| 21 | `app/core/mcp/server.py` | FastMCP server：6 个工具 + MCPState 共享状态 |
| 22 | `app/core/mcp/tools.py` | MCP 工具 → LangChain Tool 包装，`include` 参数筛选 |

---

## 第六遍：RAG 底层

> 先理解 KnowledgeAgent 调用 search_documents，再看 search_documents 内部怎么实现

| 顺序 | 文件 | 看点 |
|------|------|------|
| 23 | `app/core/rag_engine.py` | SearchContext / AnalysisContext → embedding(k=60) → 阈值 0.92 → 关键词补充 → 多样性 → 文件名回退 |
| 24 | `app/core/vector_store.py` | ChromaDB 封装：add / search / delete / compact / keyword_search |
| 25 | `app/core/document_processor.py` | 5 种格式 Loader + 切片策略 |

---

## 第七遍：记忆系统

> 辅助能力，不是主流程

| 顺序 | 文件 | 看点 |
|------|------|------|
| 26 | `app/core/agent_memory.py` | **线程安全**：`RLock` 保护 `_sessions`；里程碑压缩 + 事实提取 + 偏好检测。公共 API：`has_session()` / `restore_session()` |
| 27 | `app/core/redis_store.py` | 只读封装：get_recent_history / get_memory，写入由 Java 负责 |

---

## 第八遍：API 整合

> 入口包装，最后看

| 顺序 | 文件 | 看点 |
|------|------|------|
| 28 | `app/api/qa.py` | `POST /qa/ask`：request → Context → Orchestrator → Response（TaskGraph → list[dict] 序列化） |
| 29 | `app/api/ingestion.py` | `POST /ingest/document` + `DELETE /ingest/document/{id}` |
| 30 | `app/stream_consumer.py` | RabbitMQ 消费者：MinIO 下载 → ingest → 状态更新 |

---

## 第九遍：单元测试

> 85 个测试覆盖核心模块，验证数据契约和边界条件

| 顺序 | 文件 | 看点 |
|------|------|------|
| 31 | `tests/conftest.py` | 共享 fixtures：make_context / mock_llm / mock_vector_store |
| 32 | `tests/test_agent_context.py` | 写保护 + reset_for_retry — 15 个测试 |
| 33 | `tests/test_agent_registry.py` | 注册 + DAG 校验（循环/冲突/缺失依赖）— 18 个测试 |
| 34 | `tests/test_extract_json.py` | JSON 提取：直接/代码块/深度匹配 — 11 个测试 |
| 35 | `tests/test_rag_engine_parsers.py` | 过滤器 + bigrams + top_k + diversity — 18 个测试 |
| 36 | `tests/test_agent_memory_unit.py` | 会话管理 + 淘汰 + 序列化 + 并发安全 — 18 个测试 |

```bash
python -m pytest tests/ -v  # 运行全部 85 个测试
```

---

## 架构主线（面试必画）

```
User Question
  → Coordinator（是否需要复杂流程？）
    → Planner（怎么拆任务？）
      → TaskGraph（DAG）
        → AgentRegistry（validate_dag + 能力查找）
          → Agents（按拓扑执行，requires 校验）
            → AgentContext（写保护数据契约）
              → AnswerGenerator（统一生成）
                → Critic（审核 + task_id 精确重试）
                  → AgentMemory（线程安全，里程碑/事实/偏好）
```

## 面试重点

- **Orchestrator** 完整流程：简单模式 vs 规划模式，Critic task_id 精确重试
- **AgentRegistry** 能力描述层：register → validate_dag（输出冲突 + 循环检测 + requires）→ find_by_tool/writes
- **AgentContext** 写保护：set_evidence() 数据所有权设计 + reset_for_retry() 重试清空
- **TaskGraph**：拓扑调度、BFS 下游依赖、输出冲突检测
- **Critic 失败安全**：LLM 异常 → score=0 + need_retry=True，不静默放行
- **rag_engine** 搜索流程：embedding → 阈值 → 关键词 → 多样性 → 回退
- **MCP**：Agent 不知道 RAG，Agent 只知道 Tool
- **AgentMemory**：RLock 线程安全 + 里程碑压缩 + 事实提取 + LRU 淘汰
- **LLM 工厂**：create_llm() 消除 4 处重复，统一配置入口

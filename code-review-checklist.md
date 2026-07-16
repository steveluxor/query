# Python AI 服务 — 代码复盘清单

> 系统已从 RAG 项目升级为 Agent 平台，复盘重点：Orchestrator + Registry + Context + TaskGraph

---

## 第一遍：建立运行模型

### 1. 配置与入口

| 顺序 | 文件 | 看点 |
|------|------|------|
| 1 | `app/config.py` | LLM / Embedding / Redis / MCP / 外部服务依赖 |
| 2 | `app/main.py` | 启动顺序：VectorStore → RAGEngine → Memory → MCPClient → Agents → Orchestrator（决定谁依赖谁） |
| 3 | `app/core/prompt_manager.py` | 加载 prompts.yaml，`get(*keys)` 按 key 链获取 |
| 4 | `app/core/prompts.yaml` | 所有 Agent 的 prompt 驱动：planner / critic / knowledge / analysis |

---

## 第二遍：核心数据契约

> 先理解 Agent 之间"说什么话"，再看 Agent 怎么工作

### 2. 数据结构

| 顺序 | 文件 | 看点 |
|------|------|------|
| 5 | `app/models/data_types.py` | Evidence / Calculation / AnalysisResult / CriticResult / AgentTrace — **整个 Multi-Agent 的语言** |
| 6 | `app/models/task_graph.py` | TaskNode（id, agent, objective, depends_on, output_key, status）+ TaskGraph — Planner 生成，Orchestrator 执行 |
| 7 | `app/models/capability.py` | AgentCapability（name, tools, writes_to, requires）— Agent 不是简单类，而是类 + 能力声明 |
| 8 | `app/core/agent_context.py` | **所有 Agent 通信靠它**。重点：为什么用 `set_evidence()` 而不是 `context.evidence = []`（数据所有权保护） |

---

## 第三遍：Agent 核心架构

### 3. 基础设施

| 顺序 | 文件 | 看点 |
|------|------|------|
| 9 | `app/core/agents/base_agent.py` | 所有 Agent 生命周期：`execute()` → `run()` → AgentTrace |
| 10 | `app/core/agent_registry.py` | **能力描述层**（不是 dict）：register / get_agent / validate_dag / find_by_tool / find_by_writes / format_for_prompt |
| 11 | `app/core/agent_orchestrator.py` | **整个项目核心**，建议分四部分看 ↓ |

### 4. Orchestrator 拆解

| 部分 | 方法 | 看点 |
|------|------|------|
| 11a | `__init__` | Registry 创建 + Agent 实例绑定 |
| 11b | `run()` | 主流程 9 步：memory → coordinator → plan? → execute → generator → critic → memory |
| 11c | `_plan()` / `_execute_plan()` / `_validate_task_graph()` | DAG 执行：拓扑调度 + requires 校验 + 输出冲突检测 |
| 11d | `_retry_from_task()` / `_run_critic_with_retry()` | **面试最大亮点**：Critic 精确定位 task_id → BFS 下游 → 拓扑重跑 |

---

## 第四遍：Agent 实现

### 5. 各 Agent

| 顺序 | 文件 | 看点 |
|------|------|------|
| 12 | `app/core/agents/coordinator_agent.py` | **不是 Planner**。Coordinator 决定"是否需要复杂流程"，Planner 决定"怎么拆任务" |
| 13 | `app/core/agents/knowledge_agent.py` | Tool calling → raw result → Evidence 提取 → `context.set_evidence()`。先理解角色，不深入 RAG |
| 14 | `app/core/agents/analysis_agent.py` | `requires=["evidence"]` → tool 计算 → AnalysisResult |
| 15 | `app/core/generator/answer_generator.py` | 为什么独立：Evidence + Analysis → Generator → Answer（以前各 Agent 自己生成，现在统一出口） |
| 16 | `app/core/agents/critic_agent.py` | 审核 → 定位问题 → retry_target → 重新执行。prompt 含任务图，支持 task_id 精确重试 |

---

## 第五遍：MCP 工具链

> Agent 不知道 RAG，Agent 只知道 Tool

| 顺序 | 文件 | 看点 |
|------|------|------|
| 17 | `app/core/mcp/client.py` | StdioClientTransport → ClientSession，call_tool() / list_tools() |
| 18 | `app/core/mcp/server.py` | FastMCP server：6 个工具 + MCPState 共享状态 |
| 19 | `app/core/mcp/tools.py` | MCP 工具 → LangChain Tool 包装，`include` 参数筛选 |

---

## 第六遍：RAG 底层

> 先理解 KnowledgeAgent 调用 search_documents，再看 search_documents 内部怎么实现

| 顺序 | 文件 | 看点 |
|------|------|------|
| 20 | `app/core/rag_engine.py` | SearchContext / AnalysisContext → embedding(k=60) → 阈值 0.92 → 关键词补充 → 多样性 → 文件名回退 |
| 21 | `app/core/vector_store.py` | ChromaDB 封装：add / search / delete / compact / keyword_search |
| 22 | `app/core/document_processor.py` | 5 种格式 Loader + 切片策略 |

---

## 第七遍：记忆系统

> 辅助能力，不是主流程

| 顺序 | 文件 | 看点 |
|------|------|------|
| 23 | `app/core/agent_memory.py` | 里程碑压缩 + 事实提取 + 偏好检测。为什么 Redis 不是 source of truth，Python Memory 才是运行状态 |
| 24 | `app/core/redis_store.py` | 只读封装：get_recent_history / get_memory，写入由 Java 负责 |

---

## 第八遍：API 整合

> 入口包装，最后看

| 顺序 | 文件 | 看点 |
|------|------|------|
| 25 | `app/api/qa.py` | `POST /qa/ask`：request → Context → Orchestrator → Response（TaskGraph → list[dict] 序列化） |
| 26 | `app/api/ingestion.py` | `POST /ingest/document` + `DELETE /ingest/document/{id}` |
| 27 | `app/stream_consumer.py` | RabbitMQ 消费者：MinIO 下载 → ingest → 状态更新 |

---

## 架构主线（面试必画）

```
User Question
  → Coordinator（是否需要复杂流程？）
    → Planner（怎么拆任务？）
      → TaskGraph（DAG）
        → Registry（校验 + 查找）
          → Agents（按拓扑执行）
            → AgentContext（数据契约）
              → Generator（统一生成）
                → Critic（审核 + 精确重试）
                  → Memory（持久化）
```

## 面试重点

- **Orchestrator** 完整流程：简单模式 vs 规划模式，Critic task_id 精确重试
- **AgentRegistry** 职责：能力声明、DAG 校验（输出冲突 + 循环检测 + requires）、按能力查找
- **AgentContext** 写保护：为什么用 setter，数据所有权设计
- **TaskGraph**：拓扑调度、BFS 下游依赖、输出冲突检测
- **rag_engine** 搜索流程：embedding → 阈值 → 关键词 → 多样性 → 回退
- **MCP**：Agent 不知道 RAG，Agent 只知道 Tool
- **AgentMemory**：里程碑压缩 + 事实提取，Redis 是持久层但 Python 是运行状态

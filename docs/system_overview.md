# RAG 知识库问答系统 — 技术架构总结

## 系统概述

这是一个基于 RAG（检索增强生成）的智能知识库问答系统，支持文档上传、向量化存储、智能问答、记忆管理、偏好系统、反思机制和规划能力。

## 技术栈

| 组件 | 技术 | 端口 |
|------|------|------|
| 前端 | Nginx + HTML/CSS/JS | :8080 |
| Java 后端 | Spring Boot 4.0.6 + MyBatis | :8085 |
| Python AI 服务 | FastAPI + LangChain + ChromaDB | :8000 |
| 向量数据库 | ChromaDB（本地持久化） | - |
| Embedding 模型 | Ollama nomic-embed-text | :11434 |
| LLM | DeepSeek API (deepseek-chat) | - |
| 缓存/记忆 | Redis | :6379 |

**请求流向：** 前端(:8080) → Nginx → Java(:8085) → Python(:8000)

## 核心功能模块

### 1. RAG 检索引擎 (`app/core/rag_engine.py`)

**架构：** LangChain Agent + Tool Calling

**工具列表：**
- `search_documents(query, row_start?, row_end?)` — 向量搜索，余弦距离阈值 0.85
- `calculate_sum(key, filter?)` — 数值求和
- `calculate_rank(key, ascending, position, filter?)` — 排名查询
- `read_all_rows()` — 读取完整数据
- `list_documents()` — 列出可用文档

**流程：**
1. 用户提问 → LLM 决定调用哪些工具 → 工具执行 → LLM 生成最终答案
2. 工具调用限制：搜索最多 2 次，计算工具最多 8 次

**关键配置：**
- 余弦距离阈值：0.85
- 默认 top_k：10
- 历史轮数：5 轮
- Agent 递归限制：30

### 2. AgentMemory 记忆系统 (`app/core/agent_memory.py`)

**数据模型：**
```python
@dataclass
class SessionMemory:
    session_id: str
    turn_count: int
    milestones: list[Milestone]      # 里程碑摘要
    facts: list[Fact]                # 关键事实
    preferences: dict                # 用户偏好
    _dirty: bool
    _preferences_dirty: bool
```

**三个维度：**

| 维度 | 说明 | 更新频率 |
|------|------|---------|
| 里程碑 | 每 N 轮的对话摘要 | 每 10 轮或累积 3 个事实 |
| 事实 | 关键操作记录（排序、筛选、聚合等） | 每轮 |
| 偏好 | 用户偏好设置（称呼、格式、风格等） | 每轮（LLM 检测） |

**偏好系统：**
- LLM 检测偏好变化（`_check_preference_changes`）
- 支持新增、修改、删除偏好
- 删除偏好时添加三层取消指令（防止 LLM 执行历史旧偏好）
- 跳过优化：偏好为空时跳过 LLM 调用

**关键配置：**
- REWRITE_INTERVAL = 10（每 10 轮触发 LLM 重写）
- LLM 模型：deepseek-chat
- 温度：0.1

### 3. 反思机制 (`rag_engine.py` — `_reflect()`)

**流程：**
1. Agent 生成初始答案
2. 评估器 LLM 判断答案质量
3. 如果评估为"需要修改"，带反馈重新生成
4. 最多重试 2 次

**触发条件：**
- `reflection_enabled = True`
- 有工具调用（简单问候跳过）

**评估标准：**
- 准确性：是否基于检索数据，有无编造
- 完整性：是否回答了用户问题
- 相关性：是否切题

### 4. 规划能力 (`rag_engine.py` — `_plan()`, `_execute_step()`, `_replan()`)

**流程：**
1. Planner 生成步骤列表
2. 逐步执行，每步后 Replanner 评估是否需要调整
3. 所有步骤完成后，生成最终答案

**当前状态：** 已实现但默认关闭（`planning_enabled = False`）

**适用场景：** 复杂多步任务（如"比较万代和高高品牌"）

### 5. 文档处理 (`app/core/document_processor.py`)

**支持格式：** PDF、DOCX、TXT、MD、XLSX

**切片策略：**
- 非 Excel：RecursiveCharacterTextSplitter（1000 字符/200 重叠）
- Excel：每行视为独立 chunk

### 6. Redis 存储 (`app/core/redis_store.py`)

**存储内容：**
- 对话历史：`qa:history:{session_id}`
- AgentMemory：`qa:memory:{session_id}`

**TTL：** 24 小时

## API 接口

### 问答接口
```
POST /qa/ask
请求：
{
  "question": "问题文本",
  "session_id": "会话ID（可选）",
  "document_ids": [1, 2]（可选，指定文档）,
  "top_k": 5,
  "history": [{"question": "...", "answer": "..."}],
  "strategy": "relevance/diversity/null",
  "preferences": {"address_as": "老师"}
}

响应：
{
  "answer": "回答文本",
  "sources": [{"document_id": 1, "file_name": "...", "content": "...", "score": 0.8}],
  "is_agg": false,
  "tools_called": ["search_documents", "calculate_sum"],
  "session_id": "会话ID",
  "memory_data": {"milestones": [...], "facts": [...], "preferences": {...}},
  "reflection_count": 0,
  "plan": ["步骤1", "步骤2"]（规划模式时）
}
```

### 文档导入接口
```
POST /ingest/document
DELETE /ingest/document/{id}
```

## LLM 调用次数分析

| 场景 | 调用次数 | 说明 |
|------|---------|------|
| 简单问候 | 2 次 | 偏好检测 + 主生成 |
| 单步查询 | 3+ 次 | 偏好检测 + 主生成 + 工具调用 |
| 复杂计算 | 4+ 次 | 偏好检测 + 主生成 + 多次工具调用 |
| 触发反思 | +1-2 次 | 评估 + 重新生成 |
| 触发规划 | +3-5 次 | plan + replan × N + final |
| 记忆重写（每10轮） | +1 次 | _rewrite_summary_and_extract |

## 配置项

```python
# LLM
llm_api_key: str
llm_base_url: str = "https://api.deepseek.com"
llm_model_name: str = "deepseek-chat"

# Embedding
embedding_model_name: str = "nomic-embed-text"
ollama_base_url: str = "http://localhost:11434"

# Reflection
reflection_enabled: bool = True
max_reflection_retries: int = 2

# Planning
planning_enabled: bool = False

# Redis
redis_host: str = "localhost"
redis_port: int = 6379
```

## 文件结构

```
app/
├── main.py                  # FastAPI 入口
├── config.py                # 配置管理
├── prompts.yaml             # 提示词模板
├── prompt_manager.py        # 提示词加载器
├── api/
│   ├── ingestion.py         # 文档导入 API
│   └── qa.py                # 问答 API
├── core/
│   ├── rag_engine.py        # RAG 引擎（Agent + 工具 + 反思 + 规划）
│   ├── agent_memory.py      # 记忆系统（里程碑 + 事实 + 偏好）
│   ├── vector_store.py      # 向量数据库封装
│   ├── document_processor.py # 文档处理
│   └── redis_store.py       # Redis 存储
├── models/
│   └── schemas.py           # Pydantic 数据模型
└── exceptions.py            # 统一异常处理

tests/
├── test_preference.py       # 偏好测试
├── test_preference_v2.py    # 偏好生命周期测试
├── test_preference_reassign.py # 偏好替换测试
├── test_reflection.py       # 反思测试
└── reports/                 # 测试报告
```

## 当前系统能力总结

1. **RAG 检索** — 向量搜索 + 工具调用
2. **记忆系统** — 里程碑、事实、偏好
3. **偏好管理** — LLM 检测、三层取消指令
4. **反思机制** — 答案自评估、自动重试
5. **规划能力** — 任务分解、逐步执行（已实现但关闭）
6. **Redis 持久化** — 对话历史和记忆存储
7. **并行执行** — 偏好检测与主生成并行

## 潜在扩展方向

1. **多 Agent 协作** — 不同 Agent 处理不同任务
2. **外部工具集成** — 网页搜索、API 调用
3. **流式响应** — 实时返回生成过程
4. **可视化调试** — 执行轨迹和记忆状态展示
5. **多轮对话优化** — 更智能的上下文管理

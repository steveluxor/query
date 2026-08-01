# Query - Agent Runtime 架构 (v10: 文档摘要 + 搜索过滤 + 前端会话优化)

## 系统架构

本项目是一个 **基于 Multi-Agent 的智能知识问答系统**，由三部分组成：

| 组件 | 路径 | 技术栈 | 端口 |
|------|------|--------|------|
| 前端 | `D:\DOWNLOAD\nginx-query` | Nginx + 原生 HTML/CSS/JS | :8080 |
| Java 后端 | `D:\IntelliJ IDEA 2025.1.3\project\Query` | Spring Boot 4.0.6 + MyBatis | :8085 |
| Python AI 服务 | 本项目 (`D:\DOWNLOAD\pycharm\query`) | FastAPI + LangChain + ChromaDB + MCP | :8000 |

**请求流向：** 前端(:8080) → Nginx 反向代理 → Java 后端(:8085) → Python AI 服务(:8000)

本项目是系统的 AI 核心，采用 **Agent Runtime 架构**，通过 **AgentCapability 声明式契约** 驱动 Agent 生命周期。

---

## 从 v9 到 v10 的变更

### 核心变更

| 变更 | v9 | v10 |
|------|----|----|
| 项目名称 | RAG 知识管理系统 | **智能知识问答系统**（前端标题、Logo "RAG"→"AI"） |
| 文档摘要 | — | **[v10 新增]** 上传文档时 LLM 生成摘要，存储于 MySQL + Redis |
| 搜索过滤 | 无过滤 | **[v10 新增]** MCP Server 层 LLM 相关性判断，过滤无关文档 |
| 摘要缓存 | — | **[v10 新增]** `DocumentSummaryCache`：Redis 逻辑过期 + Java API 回源 |
| Java Summary API | — | **[v10 新增]** `GET/PUT /document/{id}/summary` |
| MySQL schema | — | **[v10 新增]** document 表加 `summary TEXT` 字段 |
| 前端会话管理 | 立即写库、可连创、切换丢对话 | **[v10 修改]** 延迟写库、防重复创建、切换保留对话 |
| Java 鉴权 | summary 端点需鉴权 | **[v10 修改]** `/document/*/summary` 加入 excludePathPatterns |
| prompts.yaml | 无 summary/relevance | **[v10 新增]** `summary.generate` + `relevance.judge` prompt |

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

v9 (CodeAgent 代码执行 + 图表生成):
  [v9: 新增] CodeAgent: LLM 生成 Python 代码 → 沙箱执行 → 结果返回
  [v9: 新增] CodeExecutor: 独立子进程沙箱（模块白名单/内存限制/超时）
  [v9: 新增] CodeResult: code/output/stdout/error/success/image_paths/image_data
  [v9: 新增] 图片全链路: Python PNG → base64 → Java MinIO → 前端展示
  [v9: 新增] ChartController: GET /charts/{objectName} 图片代理
  [v9: 新增] WebMvcConfig: /charts/** 免鉴权
  [v9: 新增] Nginx: /charts/ 反向代理到 Java 后端
  [v9: 新增] 前端: markdown 表格渲染 + 图片容器 + 响应式样式
  [v9: 修改] Generator: 图表已生成时不再提及文件路径
  [v9: 修改] Planner: code agent 使用规则 + port_bindings 示例
  [v9: 修改] Code prompt: 饼图防重叠规则（合并小占比为"其他"）
  [v9: 修改] Dockerfile: 添加 fonts-wqy-zenhei 中文字体
  [v9: 修改] pyproject.toml: +matplotlib, mcp<2.0

v10 (文档摘要 + 搜索过滤 + 前端会话优化):
  [v10: 新增] DocumentSummaryCache: Redis 逻辑过期 + Java API 回源的文档摘要缓存
  [v10: 新增] 文档摘要生成: 上传时 LLM 读取全文生成 2-3 句摘要
  [v10: 新增] 搜索相关性过滤: MCP Server 层 LLM 批量判断文档摘要与问题相关性
  [v10: 新增] Java Summary API: GET/PUT /document/{id}/summary（免鉴权）
  [v10: 新增] MySQL: document 表加 summary TEXT 字段
  [v10: 新增] prompts.yaml: summary.generate + relevance.judge prompt 模板
  [v10: 修改] 项目名称: RAG 知识管理系统 → 智能知识问答系统
  [v10: 修改] 前端 Logo: "RAG" → "AI"
  [v10: 修改] 前端会话管理: 延迟写库（发送首条消息后才创建）+ 防重复创建 + 切换保留对话
  [v10: 修改] Java WebMvcConfig: /document/*/summary 加入 excludePathPatterns
  [v10: 修改] Document 实体: +summary 字段
  [v10: 修改] DocumentMapper.xml: insert/update 加 summary
```

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
    │   ├── ingestion.py         # 文档向量化 API [+v10: 上传后生成摘要]
    │   └── qa.py                # 问答 API（含 image_urls 传递）
    ├── core/
    │   ├── agent_context.py     # Agent 共享上下文
    │   ├── agent_memory.py      # 会话记忆管理
    │   ├── agent_orchestrator.py # Agent 编排器
    │   ├── agent_registry.py    # Agent 能力注册表（含 CodeAgent）
    │   ├── code_executor.py     # 沙箱化 Python 代码执行器
    │   ├── document_processor.py # 文档解析/切片
    │   ├── rag_engine.py        # RAG 核心引擎
    │   ├── workflow_validator.py # DAG 校验器（六层）
    │   ├── utils.py             # 工具函数 (extract_json)
    │   ├── log_config.py        # JSON 结构化日志
    │   ├── infra/               # 基础设施层
    │   │   ├── redis_store.py   # Redis 读取封装
    │   │   ├── vector_store.py  # 向量数据库
    │   │   ├── llm_factory.py   # LLM 工厂函数
    │   │   └── summary_cache.py # [v10 新增] 文档摘要缓存（Redis 逻辑过期 + Java API 回源）
    │   ├── prompts/             # 提示词管理
    │   │   ├── prompt_manager.py # 提示词管理器
    │   │   └── prompts.yaml     # 提示词模板 [+v10: summary.generate + relevance.judge]
    │   ├── actions/             # Action 注册与分发（模块化）
    │   │   ├── __init__.py      # ActionRegistry + create_default
    │   │   ├── base.py          # ActionHandler 抽象基类
    │   │   ├── retry.py         # RetryHandler
    │   │   └── terminate.py     # TerminateHandler
    │   ├── mcp/
    │   │   ├── client.py        # MCP Client
    │   │   ├── server.py        # MCP Server [+v10: search_documents 过滤]
    │   │   ├── session_manager.py # Session 管理
    │   │   └── tools.py         # LangChain 工具封装
    │   ├── agents/
    │   │   ├── base_agent.py    # BaseAgent + ControllerAgent 基类
    │   │   ├── chat_agent.py    # 问候/闲聊（LLM 化）
    │   │   ├── retrieval_agent.py  # 知识检索（代码控制搜索 + 全量加载）
    │   │   ├── extraction_agent.py # 知识提取（纯 LLM，Map-Reduce）
    │   │   ├── analysis_agent.py   # 数据分析
    │   │   ├── code_agent.py       # 代码执行（LLM 生成 + 沙箱执行）
    │   │   └── critic_agent.py     # 答案审核 (ControllerAgent)
    │   └── generator/
    │       └── answer_generator.py # 答案生成（含 code_result 支持）
    └── models/
        ├── capability.py        # AgentCapability（含 dedup_key_func）
        ├── control.py           # ControlAction
        ├── task_graph.py        # TaskGraph（含 port_bindings）
        ├── mcp_session.py       # MCPSession
        ├── data_types.py        # Evidence, AnalysisResult, CodeResult 等
        └── schemas.py           # Pydantic 模型（含 image_urls）
```

---

## [v10 新增] 文档摘要与搜索过滤

### 问题背景

搜索时拉入了无关文档（如"账.xlsx"），导致 Extractor 处理 7 个文档（166 个知识对象），耗时长且质量差。在文档上传时生成摘要，用于搜索时判断文档相关性，可以过滤无关文档。

### 设计原则

- **Python 后端无状态**：不维护任何进程内缓存/状态，所有状态存 Redis/MySQL
- **逻辑过期**：Redis 中存储 `expire_at` 时间戳，读取时检查是否过期，过期则回源 MySQL 重新加载
- **一次成本**：生成摘要时读取文档全部内容（而非前 3 个 chunk），LLM 调用是一次性开销
- **LLM 相关性判断**：搜索时用 LLM 批量判断文档摘要是否与问题相关

### 架构

```
上传文档 → Python 读取全部内容 → LLM 生成摘要 → 调 Java API 写入 MySQL + Redis
搜索时 → Python 查 Redis(逻辑过期) → 命中且未过期直接用，过期/未命中 → Java API 查 MySQL → 回写 Redis
搜索过滤 → Python 收集候选文档摘要 → LLM 批量判断相关性 → 过滤无关文档
删除/重传文档 → Python 调 Java API 删除摘要 → 清理 Redis
```

### DocumentSummaryCache — Redis 逻辑过期 + Java API 回源

**文件**: `app/core/infra/summary_cache.py`

```python
EXPIRE_MIN = 2 * 24 * 3600   # 2天（逻辑过期下限）
EXPIRE_MAX = 3 * 24 * 3600   # 3天（逻辑过期上限，随机防雪崩）
RENEW_TTL = 7 * 24 * 3600    # Redis 物理 TTL 7天（兜底）

class DocumentSummaryCache:
    """文档摘要缓存：Redis 逻辑过期 + MySQL 回源

    Redis value: {"summary": "...", "expire_at": timestamp}
    无进程内状态，Python 后端保持无状态
    """

    async def get(self, document_id: int) -> str | None:
        """获取摘要：Redis 命中且未过期 → 直接返回；过期/未命中 → 回源 Java API (MySQL)"""
        # 1. 查 Redis → 检查 expire_at → 未过期则刷新 TTL 并返回
        # 2. 回源 Java API GET /document/{id}/summary
        # 3. 写入 Redis（逻辑过期 + 物理 TTL）

    async def set(self, document_id: int, summary: str):
        """写入摘要（Java API PUT + Redis）"""

    async def delete(self, document_id: int):
        """删除摘要（Redis 删除，MySQL 随文档删除）"""

    async def get_batch(self, document_ids: list[int]) -> dict[int, str]:
        """批量获取摘要"""
```

### 搜索过滤流程

**文件**: `app/core/mcp/server.py`

```python
# search_documents 工具中，_execute_search 之后：
async def search_documents(query, row_start, row_end):
    # 1. 执行原始搜索
    result = rag_engine.execute_search(query, row_start, row_end, state)

    # 2. 从结果中提取文档 ID
    doc_ids = {row["document_id"] for row in data["data"]}

    # 3. 批量获取摘要
    summaries = await summary_cache.get_batch(list(doc_ids))

    # 4. LLM 判断相关性
    relevant_ids = await _judge_relevance(query, summaries)

    # 5. 过滤结果（如果全部过滤掉则回退到原始结果）
    filtered = [row for row in data["data"] if row["document_id"] in relevant_ids]
    return json.dumps(filtered_data) if filtered else result

async def _judge_relevance(query, summaries):
    """LLM 批量判断文档相关性"""
    prompt = RELEVANCE_PROMPT.format(question=query, summaries=summaries_text)
    response = await llm.ainvoke(prompt)
    # 解析 JSON: {"relevant_ids": [1, 2, 5]}
```

**关键点：**
- 过滤在 MCP Server 层执行（异步），不影响 `_execute_search`（同步）
- 过滤后如果全部被移除，回退到原始结果（防止误杀）
- LLM 判断只在有摘要时执行，无摘要时跳过过滤

### Ingestion 生成摘要

**文件**: `app/api/ingestion.py`

```python
# ingest_document 成功后：
async def ingest_document(request, vector_store, summary_cache):
    # ... 向量化完成后 ...
    # 读取全部 chunk 内容，调 LLM 生成摘要
    all_text = "\n".join([c.text for c in chunks])
    summary = await _generate_summary(all_text, document_id)
    if summary:
        await summary_cache.set(request.document_id, summary)

async def _generate_summary(content, document_id):
    """截断到 8000 字符，调 LLM 生成 2-3 句摘要"""
    truncated = content[:8000]
    prompt = PromptManager.get("summary", "generate").format(content=truncated)
    response = await llm.ainvoke(prompt)
    return response.content.strip()
```

### Java Summary API

**文件**: `java/.../controller/DocumentController.java`

```java
@GetMapping("/{id}/summary")
public Result getSummary(@PathVariable("id") Long documentId) {
    Document doc = documentMapper.selectById(documentId);
    return Result.ok(doc != null ? doc.getSummary() : null);
}

@PutMapping("/{id}/summary")
public Result updateSummary(@PathVariable("id") Long documentId,
                            @RequestBody Map<String, String> body) {
    Document doc = new Document();
    doc.setId(documentId);
    doc.setSummary(body.get("summary"));
    documentMapper.updateDocument(doc);
    return Result.ok();
}
```

**免鉴权**: `WebMvcConfig.java` 的 `excludePathPatterns` 加入 `/document/*/summary`。

### MySQL Schema

**文件**: `sql/init.sql`

```sql
ALTER TABLE document ADD COLUMN summary TEXT COMMENT '文档摘要（Python 生成）';
```

### prompts.yaml 新增

**文件**: `app/core/prompts/prompts.yaml`

```yaml
summary:
  generate: |
    你是一个文档摘要生成器。请为以下文档内容生成简洁准确的摘要。
    摘要要求：
    - 2-3 句话概括文档的核心主题和关键内容
    - 包含文档中的重要数据、名称、结论
    - 长度不超过 300 字
    - 只输出摘要文本，不要任何标题或说明
    文档内容：
    {content}

relevance:
  judge: |
    你是一个文档相关性判断器。根据用户问题和每个文档的摘要，判断哪些文档与问题相关。
    用户问题：{question}
    文档摘要：
    {summaries}
    规则：
    - 只返回与问题直接相关的文档 ID
    - 如果不确定，倾向于保留（宁可多处理，不要漏掉）
    - 返回 JSON 格式：{"relevant_ids": [1, 2, 5]}
    - 只返回 JSON，不要解释
```

### 数据流总结

```
上传:
  ingestion.py → DocumentProcessor.process() → add_texts (Chroma)
               → _generate_summary() → LLM
               → summary_cache.set() → Java PUT /document/{id}/summary (MySQL) + Redis SET

搜索:
  MCP server.search_documents → rag_engine.execute_search()
                               → summary_cache.get_batch(doc_ids)
                               → _judge_relevance() → LLM
                               → 过滤 results

删除:
  MCP server.delete_document → summary_cache.delete() → Redis DEL
                             → Java DELETE (MySQL 随文档删除)
```

---

## [v10 修改] 前端会话管理

### 问题背景

三个前端 bug：
1. 切换页面再切回来，丢失当前对话
2. 新建任务出现在列表中间而非顶部
3. 可以连续创建多个新任务（应只允许一个）
4. 新对话立即写入数据库，可以被删除

### 解决方案：延迟写库 + 会话状态管理

引入 `state.pendingSession`：本地暂存的新会话，不调 API，不写数据库。只有用户发送消息时才真正创建。

**State 新增字段:**

```js
const state = {
    // ... 原有字段 ...
    _creatingSession: false,       // 防重复点击
    sessionHasMessages: false,     // 当前会话是否有消息
    pendingSession: null,          // 本地暂存的新会话
    _pendingUserBubble: null,      // 进行中的用户气泡 HTML
    _pendingLoadingBubble: null,   // 进行中的 loading HTML
    _awaitingFirstResponse: false, // 等待首次 API 响应
};
```

### 会话生命周期

```
用户点击"新建任务"
  → handleNewSession() → state.pendingSession = { id: 'pending-1', ... }
  → 按钮禁用，不调 API

用户发送消息
  → handleSendQuestion() → Api.createSession() 写库
  → state.currentSessionId = real_id
  → Api.ask() → loadQaHistory()

用户切换到文档页再切回
  → switchToQa() → 检查 _awaitingFirstResponse
  → 如果为 true → 跳过 loadQaHistory()，DOM 保留用户气泡+loading

API 响应到达
  → loadQaHistory() → renderHistoricalMessages()
  → 清除 pending 状态
```

### 关键函数改动

| 函数 | 改动 |
|------|------|
| `handleNewSession()` | 不调 API，本地创建 pending session，按钮禁用 |
| `handleSendQuestion()` | pending→real 转换：先 `createSession()` 再 `ask()`；设 `_awaitingFirstResponse` |
| `renderSessionList()` | 合并 `pendingSession` + `state.sessions` 渲染 |
| `switchToQa()` | pending 且 `_awaitingFirstResponse` 时跳过 `loadQaHistory()` |
| `switchToDoc()` | 无特殊处理（DOM 天然保留） |
| `handleSwitchSession()` | 切换时丢弃 pending session |
| `handleDeleteSession()` | pending 仅清除本地，不调 API |
| `loadQaHistory()` | 根据消息数设置 `sessionHasMessages`，控制按钮状态 |
| `renderHistoricalMessages()` | 渲染后追加 pending bubbles（如果存在） |

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

### merge_policy 策略

| 策略 | 语义 | 适用 |
|------|------|------|
| `"replace"` | 直接替换（默认） | analysis, answer, critique, code_result |
| `"dedup"` | 去重合并（需配合 dedup_key_func） | evidence, sources |
| `"append"` | 直接追加 | document_bundle, knowledge_objects |

### 各 Agent Capability 一览

| Agent | inputs | required_inputs | outputs | tools | merge_policy |
|-------|--------|----------------|---------|-------|--------------|
| Chat | `{}` | `set()` | answer | 无 | — |
| Retrieval | `{}` | `set()` | document_bundle, retrieval_report | search_documents, read_all_rows | document_bundle=append |
| Extractor | `{"knowledge_document": DocumentBundle}` | `{"knowledge_document"}` | knowledge_objects, evidence, sources | 无 | knowledge_objects=append, evidence=dedup, sources=dedup |
| Analysis | `{}` | `set()` | analysis | calculate_sum, calculate_rank | analysis=replace |
| Code | `{"document_bundle": DocumentBundle}` | `{"document_bundle"}` | code_result | 无 | code_result=replace |
| Generator | `{"structured_knowledge": list, "evidence_list": list, "source_meta": list, "analysis_result": AnalysisResult \| None, "code_result": CodeResult \| None}` | `{"structured_knowledge", "evidence_list", "source_meta"}` | answer | 无 | answer=replace |
| Critic (ControllerAgent) | `{"evidence_list": list, "generated_answer": str, "retrieval_report": RetrievalReport, "analysis_result": AnalysisResult \| None}` | `{"evidence_list", "generated_answer", "retrieval_report"}` | critique, need_retry, retry_target | 无 | critique=replace |

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

## TaskGraph — DAG 任务图

```python
@dataclass
class TaskNode:
    id: str                      # "task1"
    agent: str                   # "retrieval" / "extractor" / "analysis" / "code" / "generator"
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
# v8+ — 仅 port_bindings
for port_name, source_ref in task.port_bindings.items():
    if "." not in source_ref:
        continue
    source_task_id, output_key = source_ref.split(".", 1)
    entry = context.get_output_entry(output_key, task_id=source_task_id)
    if entry and entry.value is not None:
        upstream_kwargs[port_name] = entry.value  # ← 端口名 = 参数名
```

---

## CodeAgent — 代码执行

### 架构

```
Planner 生成 DAG
  └── CodeAgent (task)
        ├── 1. MCP read_all_rows → 获取完整数据
        ├── 2. _build_data_summary() → 数据摘要
        ├── 3. _generate_code() → LLM 生成 Python 代码
        ├── 4. CodeExecutor.execute() → 沙箱子进程执行
        │     ├── 模块白名单: json, math, matplotlib, numpy 等
        │     ├── 内存限制: 512MB (RLIMIT_AS)
        │     ├── 超时: 60s
        │     └── stdout 捕获 + result 变量提取
        ├── 5. 图片处理: PNG → base64 编码 → 清理本地文件
        └── 6. set_output("code_result", CodeResult)
```

### CodeResult 数据结构

```python
@dataclass
class CodeResult:
    code: str = ""                          # 生成的 Python 代码
    output: Any = None                      # result 变量的值
    stdout: str = ""                        # 标准输出
    error: str = ""                         # 错误信息
    success: bool = True                    # 是否执行成功
    execution_time_ms: int = 0              # 执行耗时
    retry_count: int = 0                    # 重试次数
    image_paths: list[str] = field(default_factory=list)  # 生成的图表路径（本地临时）
    image_data: list[str] = field(default_factory=list)   # base64 编码的 PNG 数据
```

### CodeExecutor 沙箱安全

```python
class CodeExecutor:
    TIMEOUT = 60           # 秒
    MAX_MEMORY_MB = 512    # MB（matplotlib/numpy 需要更多内存）

    _SAFE_MODULES = frozenset({
        "json", "math", "datetime", "collections", "itertools",
        "functools", "operator", "statistics", "re", "string",
        "decimal", "fractions", "copy", "pprint", "random",
        "enum", "dataclasses", "typing", "textwrap", "hashlib",
        "base64", "uuid", "calendar", "time",
        # 图表生成
        "matplotlib", "matplotlib.pyplot", "numpy",
    })
```

**安全措施：**
- 独立子进程执行，与主进程隔离
- `__import__` 替换为 `_safe_import`，只允许白名单模块
- `builtins` 中移除 `compile`, `exec`, `eval`
- Linux 下 `resource.setrlimit(RLIMIT_AS)` 限制虚拟内存
- `asyncio.wait_for` 超时终止
- `OPENBLAS_NUM_THREADS=4` 避免多线程内存爆炸

### 图片全链路

```
CodeAgent 生成 matplotlib 代码
  → CodeExecutor 沙箱执行 → 保存 PNG 到 /app/generated/{uuid}.png
  → CodeAgent 读取 PNG → base64 编码 → 删除本地文件
  → CodeResult.image_data = ["base64..."]
  → qa.py: image_urls = code_result.image_data
  → MultiAgentResponse.image_urls = ["base64..."]
  → Java QaServiceImpl: base64 解码 → MinIO putObject("charts/{uuid}.png")
  → MySQL: qa_history.image_urls = '["charts/uuid.png"]'
  → 前端: <img src="/charts/uuid.png">
  → Nginx → ChartController → MinIO getObject → 返回 PNG
```

**Python 端保持无状态** — 不操作 MinIO，只返回 base64 编码的图片数据。Java 负责所有存储和删除操作。

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
    def format_executors_for_prompt()                 # Planner prompt: Executor 列段
    def format_controllers_for_prompt()               # Planner prompt: Controller 列表
```

### create_default_registry(llm=None)

注册 7 个 Agent（Chat, Retrieval, Extractor, Analysis, Code, Critic, Generator），内部完成 import + 实例化 + 注册。

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

---

## Plan 后处理 (_post_process_plan)

LLM 可能不遵守 Planner prompt 规则，代码层兜底修正：

### 规则 1：数值问题强制 analysis

检测到求和/排名关键词但 plan 中无 analysis → 自动插入 analysis task，连接到 retrieval 下游，并加入 generator 的 depends_on。

### 规则 2：纯数值移除 extractor

求和/排名问题不需要 extractor 的文本提取（Extractor 耗时 90-160s）→ 移除所有 extractor task。

### 规则 3：清理失效 port_bindings

移除 extractor 后，清理所有引用已移除 task ID 的 depends_on 和 port_bindings。

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

**查询 + 计算：**
```
Retrieval → Analysis → Generator
```

**代码执行（分组统计/图表）：**
```
Retrieval → Code → Generator
```

**查询 + 计算 + 审核：**
```
Retrieval → Analysis → Generator → Critic
```

**组合 DAG（检索→分析+提取→生成）：**
```
task1 (retrieval) ──→ task2 (analysis)  ──→ task4 (generator)
                    └─→ task3 (extractor) ─┘
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
       |
  +-- DocumentSummaryCache [v10]
  +-- RedisStore
```

### MCP Server — 工具清单

| 工具 | 参数 | 说明 |
|------|------|------|
| `set_document_ids` | session_id, ids | 设置文档权限 |
| `search_documents` | session_id, query, row_start?, row_end? | 搜索文档 **[v10: +LLM 相关性过滤]** |
| `list_documents` | session_id | 列出可检索文档 |
| `calculate_sum` | session_id, key_name, row_filter?, content_filter? | 求和 |
| `calculate_rank` | session_id, key_name, ascending, position?, content_filter? | 排名 |
| `read_all_rows` | session_id | 读取完整数据（**CodeAgent 主要依赖此工具**） |

### [v10] MCP Server 初始化

```python
# main() 中新增：
redis_store = RedisStore()
summary_cache = DocumentSummaryCache(redis_store.client, settings.java_base_url)
llm = create_llm(temperature=0, max_tokens=200, timeout=30)

# search_documents 中新增：
summaries = await summary_cache.get_batch(list(doc_ids))
relevant_ids = await _judge_relevance(query, summaries)
```

---

## 模块职责

### core/ — 核心业务层

**基础设施:**
- `llm_factory.py` — `create_llm(temperature, max_tokens, timeout)` 统一创建 ChatOpenAI
- `utils.py` — `extract_json(text)` 从 LLM 输出提取 JSON（支持 markdown 代码块、大括号提取）
- `log_config.py` — JSON 结构化日志
- **`summary_cache.py`** — **[v10 新增]** 文档摘要缓存：Redis 逻辑过期 + Java API 回源

**业务模块:**
- `document_processor.py` — 文档解析/切片（1000字符/200重叠），支持 PDF/DOCX/DOC/TXT/MD/XLSX
- `vector_store.py` — Chroma + Ollama Embeddings 封装
- `rag_engine.py` — RAG 核心引擎：搜索(60→0.92→关键词→多样性)、算法计算(不调 LLM)
- `code_executor.py` — 沙箱化 Python 代码执行器（模块白名单/内存限制/超时）

**Agent Runtime:**
- `agent_orchestrator.py` — Planner → TaskGraph → auto-wire → port_bindings 注入 → 执行 → 记忆更新
- `agent_registry.py` — capability 注册 + 实例化 + prompt 生成 + 能力校验（含 CodeAgent）
- `workflow_validator.py` — WorkflowValidator + PolicyValidator + GoalValidator + DAGDataFlowValidator 六层校验
- `agent_context.py` — outputs 容器 + 线程安全 + 按 task_id 隔离 + contextvars
- `agent_memory.py` — 会话记忆：事实提取、里程碑、偏好检测、LRU 淘汰、restore_session、append_turn

**Agent 实现:**
- `base_agent.py` — BaseAgent + ControllerAgent 基类
- `chat_agent.py` — 问候/闲聊，LLM 化 + 偏好注入
- `retrieval_agent.py` — LLM 生成 query，代码控制 search_documents → read_all_rows → DocumentBundle
- `extraction_agent.py` — 纯 LLM，Map-Reduce 按文档分片并行提取 KnowledgeObject
- `analysis_agent.py` — 数值计算 + Tool Calling
- `code_agent.py` — LLM 生成 Python 代码 + 沙箱执行 + 图片 base64 编码
- `critic_agent.py` — 继承 ControllerAgent，返回 ControlAction
- `answer_generator.py` — LLM 答案生成，knowledge_objects + evidence + analysis_result + code_result 同时使用

### models/ — 数据模型

- `capability.py` — AgentCapability（inputs/required_inputs/outputs/merge_policy/dedup_key_func）
- `control.py` — ControlAction 数据类
- `task_graph.py` — TaskNode + TaskGraph（port_bindings + get_descendants + invalidate_subgraph）
- `mcp_session.py` — MCPSession（session_id, document_ids, search_ctx）
- `data_types.py` — Evidence, AnalysisResult, Calculation, CriticResult, AgentResult, DocumentBundle, DocumentChunk, KnowledgeObject, RetrievalReport, AgentTrace, CodeResult
- `schemas.py` — Pydantic 请求/响应模型（MultiAgentResponse 含 image_urls）

---

## 外部依赖服务

| 服务 | 地址 | 用途 |
|------|------|------|
| Ollama | localhost:11434 | Embedding (nomic-embed-text) |
| DeepSeek API | api.deepseek.com | LLM (deepseek-chat) |
| ChromaDB | 本地 ./chroma_db/ | 向量数据库 |
| Redis | localhost:6379 | 记忆和历史持久化 + **文档摘要缓存** |
| RabbitMQ | localhost:5672 | 文档向量化任务队列 |
| MinIO | localhost:9000 | 文档文件存储 + 图表图片存储 |

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
| 提示词模板 | `core/prompts/prompts.yaml` | knowledge/retrieval/extractor/analysis/code/generator/critic/planner **+summary+relevance** |
| LLM 创建参数 | `core/infra/llm_factory.py` | create_llm(temperature, max_tokens, timeout) |
| Agent 能力声明 | `models/capability.py` | AgentCapability (inputs/outputs/merge_policy/dedup_key_func) |
| 合并策略 | `models/capability.py` | merge_policy (dedup/replace/append) |
| 去重键规则 | `models/capability.py` | dedup_key_func per output key |
| 新增 Agent | `core/agent_registry.py` | create_default_registry() 中加 import + 注册 |
| 新增 Action | `core/actions/` | 写 Handler 子类 + create_default() 中注册 |
| Planner 规则 | `core/prompts/prompts.yaml` | planner.system |
| Plan 后处理 | `core/agent_orchestrator.py` | _post_process_plan() |
| Auto-wire | `core/agent_orchestrator.py` | _auto_wire_port_bindings() |
| DAG 结构校验 | `core/workflow_validator.py` | WorkflowValidator.validate_structure() |
| 能力校验 | `core/agent_registry.py` | validate_capabilities(plan, layers) |
| Controller 策略 | `core/workflow_validator.py` | PolicyValidator.validate_controller_usage() |
| port_bindings 校验 | `core/workflow_validator.py` | DAGDataFlowValidator.validate_port_bindings() |
| 记忆淘汰策略 | `core/agent_memory.py` | idle_ttl, max_sessions, REWRITE_INTERVAL |
| MCP session TTL | `core/mcp/session_manager.py` | SessionManager._TTL (默认 1800s) |
| 代码执行沙箱 | `core/code_executor.py` | TIMEOUT, MAX_MEMORY_MB, _SAFE_MODULES |
| Code prompt | `core/prompts/prompts.yaml` | code.system 段 |
| 图表生成规则 | `core/prompts/prompts.yaml` | code.system 图表防重叠规则 |
| 图片上传 [Java] | `QaServiceImpl.java` | base64 解码 + MinIO putObject |
| 图片代理 [Java] | `ChartController.java` | GET /charts/{objectName} |
| 图片展示 [前端] | `app.js` + `style.css` | renderQaMessages() 图片渲染 |
| **文档摘要缓存** [v10] | `core/infra/summary_cache.py` | Redis 逻辑过期 + Java API 回源 |
| **摘要生成** [v10] | `api/ingestion.py` | 上传后调 LLM 生成摘要 |
| **搜索过滤** [v10] | `core/mcp/server.py` | search_documents 后 LLM 相关性过滤 |
| **Summary API** [v10 Java] | `DocumentController.java` | GET/PUT /document/{id}/summary |
| **Summary 免鉴权** [v10 Java] | `WebMvcConfig.java` | excludePathPatterns 加 /document/*/summary |
| **Summary 字段** [v10 Java] | `Document.java` + `DocumentMapper.xml` | summary TEXT |
| **前端会话管理** [v10] | `app.js` | 延迟写库 + 防重复创建 + 切换保留对话 |

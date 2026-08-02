# Query - Agent Runtime 架构 (v11: SSE 双流 + Java 透传 + LLM 推理管线优化)

## 系统架构

本项目是一个 **基于 Multi-Agent 的智能知识问答系统**，采用 RAG (Retrieval-Augmented Generation) 技术，通过 MCP (Model Context Protocol) 协议实现工具调用。

| 组件 | 路径 | 技术栈 | 端口 |
|------|------|--------|------|
| 前端 | `nginx/html/` | Nginx + 原生 HTML/CSS/JS | :8080 |
| Java 后端 | `java/` | Spring Boot 4.0.6 + MyBatis | :8085 |
| Python AI 服务 | `app/` | FastAPI + LangChain + ChromaDB | :8000 |

**请求流向：** 前端(:8080) → Nginx → Java(:8085) → Python(:8000)

**Python 对前端不可见**，所有通信经 Java 透传。

---

## 从 v10 到 v11 的变更

### 核心变更

| 变更 | v10 | v11 |
|------|-----|-----|
| 请求模式 | 同步请求-响应 | **[v11: 修改]** 异步：`/qa/ask` 立即返回 run_id，后台异步执行 |
| SSE 传输 | 无 | **[v11: 新增]** Java SSE 透传：前端 → Java SseEmitter → Python SSE |
| Runtime 事件 | 无 | **[v11: 新增]** RuntimeEventBus：per-run pub-sub，历史缓存支持迟到订阅 |
| 前端渲染 | 等待完整响应 | **[v11: 修改]** EventSource 实时接收 DAG 状态 + Agent Trace 逐步显示 |
| Extractor 输出 | 完整句子 attributes + verbose evidence | **[v11: 修改]** 短语 attributes + 核心断言 evidence（50-80 字）|
| Critic 架构 | 单级 LLM Critic（87s） | **[v11: 修改]** 两级：RuleValidator（<100ms）+ Slim LLM Critic（5-15s）|
| Generator prompt | 全量展开 | **[v11: 修改]** token budget 截断 + analysis 精简 |
| 认证 | 无 | **[v11: 新增]** LoginInterceptor + EventSource token query parameter |

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

v11 (SSE 双流 + Java 透传 + LLM 推理管线优化):
  [v11: 新增] RuntimeEventBus: per-run pub-sub 事件总线 + 历史缓存（迟到订阅回放）
  [v11: 新增] Java SSE 透传: SseEmitter 逐行解析 Python SSE 格式并转发给前端
  [v11: 新增] SSE 双流: /qa/runtime/{run_id}（Workflow 层）+ /qa/answer/{run_id}（token 增量）
  [v11: 新增] RuleValidator: 纯确定性规则检查器（空 answer + 数值一致性 normalize）
  [v11: 新增] Token Metrics: 每个 Agent 记录 input_tokens / output_tokens / latency
  [v11: 新增] 前端实时 Trace: SSE 执行期间逐步显示 Agent 步骤，完成后移入标签页
  [v11: 新增] 前端布局: 输入栏固定 + 聊天区可滚动 + 结果区固定底部可拖拽调整高度
  [v11: 新增] EventSource token 认证: ?token= query parameter（LoginInterceptor 支持）
  [v11: 新增] plan_generated 携带 planner 信息（duration_ms + summary）
  [v11: 修改] /qa/ask: 异步模式，Python 立即返回 run_id
  [v11: 修改] Java /qa/ask: 简化为只拿 run_id，持久化移到 /qa/callback
  [v11: 修改] Java /qa/runtime + /qa/answer: SseEmitter 透传 Python SSE
  [v11: 修改] Nginx: SSE 端点全部 → Java(:8085)，不再直连 Python
  [v11: 修改] Critic 架构: 单级 LLM → 两级（RuleValidator <100ms + Slim LLM Critic 5-15s）
  [v11: 修改] Critic prompt: 删除 retrieval_report/task_plan，改为"矛盾检测"
  [v11: 修改] Generator prompt: token budget + analysis 精简
  [v11: 修改] Extractor prompt: attributes 短语化 + evidence 核心断言 50-80 字
  [v11: 修改] AgentContext: +run_id +event_bus +emit()
  [v11: 修改] Python SSE 输出: 加 event: 行（支持前端 addEventListener）
  [v11: 修改] 前端: 实时 DAG + 实时 Agent Trace + 标签页结果
```

---

## SSE 双流 + Java 透传架构

### 问题背景

v10 使用同步请求-响应：前端 POST /qa/ask → 阻塞等待 30-120s → 一次性返回完整 answer。用户体验差，无法感知进度。

### 设计目标

- 前端即时感知 Agent 执行进度（哪个 Agent 在运行）
- DAG 实时渲染 + Agent Trace 逐步显示
- Python 保持无状态，所有对外通信经 Java

### 架构

```
前端 POST /qa/ask → Java(:8085) → Python(:8000)
  → Python 立即返回 {run_id, session_id}
  → Java 转发给前端
  → 后台 asyncio.create_task(_run_and_emit)

前端同时连接两个 SSE（经 Java 透传）:
  GET /qa/runtime/{run_id}   ← Nginx → Java SseEmitter → Python SSE
  GET /qa/answer/{run_id}    ← Nginx → Java SseEmitter → Python SSE

Python 执行完成 → POST /qa/callback → Java 持久化到 MySQL/Redis/MinIO
```

**Python 对前端完全不可见**，所有请求经 Nginx → Java → Python。

### Java SSE 透传实现

**文件**: `java/.../service/QaServiceImpl.java` — `proxySse()`

```java
private SseEmitter proxySse(String pythonUrl, String streamType, String runId) {
    SseEmitter emitter = new SseEmitter(300_000L);
    CompletableFuture.runAsync(() -> {
        HttpRequest httpReq = HttpRequest.newBuilder()
                .uri(URI.create(pythonUrl))
                .header("Accept", "text/event-stream")
                .timeout(Duration.ofSeconds(300))
                .GET().build();
        HttpResponse<InputStream> response = httpClient.send(httpReq, HttpResponse.BodyHandlers.ofInputStream());

        try (BufferedReader reader = new BufferedReader(new InputStreamReader(response.body(), StandardCharsets.UTF_8))) {
            String eventType = "message";
            String data = null;
            String line;
            while ((line = reader.readLine()) != null) {
                if (line.startsWith("event: ")) {
                    eventType = line.substring(7).trim();
                } else if (line.startsWith("data: ")) {
                    data = line.substring(6);
                } else if (line.isEmpty() && data != null) {
                    emitter.send(SseEmitter.event().name(eventType).data(data));
                    eventType = "message";
                    data = null;
                }
            }
        }
        emitter.complete();
    });
    return emitter;
}
```

**关键：** 逐行解析 Python SSE 格式（`event: xxx\ndata: {...}\n\n`），保持 event name 转发，前端 `addEventListener` 正常触发。

### Nginx SSE 配置

```nginx
# SSE 端点全部走 Java
location /qa/runtime/ {
    proxy_pass http://java-backend:8085;
    proxy_http_version 1.1;
    proxy_set_header Connection '';
    proxy_buffering off;
    proxy_cache off;
    chunked_transfer_encoding off;
}

location /qa/answer/ {
    proxy_pass http://java-backend:8085;
    # 同上 SSE 配置
}
```

### RuntimeEventBus — per-run pub-sub + 历史缓存

**文件**: `app/core/runtime_event_bus.py`

```python
class RuntimeEventBus:
    """per-run pub-sub 事件总线

    设计要点：
    - 每个 run_id 支持多个 subscriber（runtime SSE + answer SSE 独立消费）
    - 历史缓存：迟到的 subscriber 可回放已发布的事件
    - 预创建 channel：/qa/ask 时先 create_channel，避免事件丢失
    - 无状态：run 结束后 cleanup
    """
    def __init__(self):
        self._channels: dict[str, list[asyncio.Queue]] = {}
        self._history: dict[str, list[RuntimeEvent]] = {}  # 历史缓存

    def subscribe(self, run_id: str) -> asyncio.Queue:
        q = asyncio.Queue()
        # 先放入历史事件，再加入 subscriber 列表
        for event in self._history.get(run_id, []):
            q.put_nowait(event)
        self._channels.setdefault(run_id, []).append(q)
        return q

    async def publish(self, event: RuntimeEvent):
        # 缓存事件（供迟到的 subscriber 回放）
        if event.run_id in self._history:
            self._history[event.run_id].append(event)
        # fan-out 到所有 subscriber
        for q in self._channels.get(event.run_id, []):
            await q.put(event)
```

**历史缓存解决的问题：** Java 透传 SSE 时，前端先拿到 run_id 再连接 SSE，中间有时间差。没有历史缓存，`plan_generated` 等早期事件会丢失。

### SSE 事件流

| 事件类型 | 层级 | 发射点 | 数据 |
|----------|------|--------|------|
| `PLAN_GENERATED` | Workflow | `_plan()` 完成 | goal, planner{duration_ms, summary}, tasks[{id, agent, objective, depends_on, status}] |
| `AGENT_STARTED` | Workflow | task 开始执行 | task_id, agent, objective |
| `AGENT_COMPLETED` | Workflow | task 完成 | task_id, agent, duration_ms, summary, tools_used |
| `AGENT_FAILED` | Workflow | task 失败 | task_id, agent, duration_ms, error |
| `RUNTIME_COMPLETED` | Workflow | 全部完成 | 完整 result |
| `TOKEN_CHUNK` | Generation | Generator streaming | token 增量 |

### 前端 SSE 流程

```
POST /qa/ask → 获得 {run_id, session_id}
  → new EventSource("/qa/runtime/" + run_id + "?token=xxx")
      → plan_generated:  渲染 DAG（pending）+ 显示 Planner 步骤
      → agent_started:   DAG 节点变蓝 + 追加实时 Trace 步骤
      → agent_completed: DAG 节点变绿 + 更新 Trace 步骤状态
      → runtime_completed: 移除实时 Trace → loadQaHistory() 加载完整结果到标签页
```

**实时 Trace 显示逻辑：**
1. `plan_generated` → 创建 `_liveTraceSteps` 数组，Planner 直接显示（已完成），其他任务 pending
2. `agent_started` → 对应步骤状态改为 running，追加显示到中间区域
3. `agent_completed` → 更新步骤状态 + 耗时 + 摘要
4. `runtime_completed` → 移除实时 Trace，最终结果加载到"执行过程"标签页

---

## LLM 推理管线优化

### 问题背景

v10 的 LLM 推理耗时瓶颈：

| Agent | 耗时 | 瓶颈原因 |
|-------|------|----------|
| Retrieval | 3s | 正常 |
| Extractor | 23.5s | 并行 ✓，但输出冗余 |
| Generator | 49s | prompt 过大 — 全部 KO attributes + evidence |
| Critic | 87.3s | prompt 极大 — 全部 evidence + answer + retrieval_report + task_plan |

### 优化策略

```
Phase 3（源端精简）: Extractor 输出短语化 + evidence 核心断言
    ↓ 精简后 Critic/Generator prompt 自动瘦身
Phase 1: RuleValidator (<100ms) + Slim LLM Critic (5-15s)
Phase 2: Generator token budget + analysis 精简
```

### Phase 3: Extractor 输出精简

| 项目 | v10 | v11 |
|------|-----|-----|
| attributes 值格式 | 完整句子 | 短语 + 信息密度 |
| evidence.statement | 冗长多句 | 核心断言，50-80 字 |
| 知识对象数量 | 不限 | 宁少勿滥（3-5 个关键属性）|

### Phase 1: RuleValidator + Slim Critic

```
Answer → RuleValidator (纯确定性, <100ms) → 通过?
                                          ↓ 不通过 → 直接重试（不调 LLM）
                                Slim LLM Critic (5-15s, slim prompt)
```

RuleValidator 做空 answer + 数值一致性检查（支持 95/95%/0.95 normalize），LLM Critic 做语义矛盾检测。

### Phase 2: Generator Prompt 瘦身

| 项目 | v10 | v11 |
|------|-----|-----|
| KnowledgeObjects | 逐个展开全部 attributes | token budget 截断（list 值 max_items=3, max_tokens=80）|
| analysis | 全量传入（含 findings） | 只传 calculations + conclusions |

---

## 核心设计亮点

### 1. 声明式 Agent Capability Contract

Orchestrator 不感知 Agent 内部逻辑，只根据 Capability（输入输出 Schema）自动完成调度。Agent 是声明式能力节点，新增 Agent 无需修改 Orchestrator。

### 2. DAG Runtime 六层校验

**LLM 决定"做什么"，Runtime 保证"怎么执行"。** LLM 输出不可直接执行，经六层校验 + 后处理兜底修正。

### 3. port_bindings 数据流

通过声明式数据通道替代 Agent 间隐式共享状态，task 间通过 port_bindings 自动注入数据。

### 4. 两级 Critic 闭环控制

```
Answer → RuleValidator (确定性, <100ms) → 通过?
                                          ↓ 不通过 → 直接重试
                                Slim LLM Critic (5-15s, 矛盾检测)
```

### 5. SSE 双流 + Java 透传

```
前端 → Nginx → Java SseEmitter → Python SSE
                                   ├── /qa/runtime/{run_id}  ← Workflow 层事件
                                   └── /qa/answer/{run_id}   ← token 增量
Python → Java /qa/callback → 持久化到 MySQL/Redis/MinIO
```

Python 无状态，所有对外通信经 Java。

### 6. CodeAgent 沙箱

进程隔离 + 资源限制（512MB + 60s）+ 模块白名单，降低 LLM 生成代码执行风险。

### 7. MCP 工具协议

标准化 Tool Interface 解耦 Runtime 与工具实现。

### 8. LLM 推理管线优化

Extractor 输出精简 → Generator/Critic prompt 自动瘦身 → Token Metrics 全链路追踪。

---

## 项目规模

| 指标 | 数值 |
|------|------|
| Python 代码 | ~4500 行 |
| Java 代码 | ~2000 行 |
| 前端代码 | ~2000 行 |
| 领域 Agent | 7 个 |
| MCP 工具 | 6 个 |
| DAG 校验层 | 6 层 |
| Docker 服务 | 9 个 |
| 数据库 | MySQL + Redis + ChromaDB |
| SSE 端点 | 2 个（经 Java 透传）|
| API 端点 | 16 个 |

---

## 部署架构

```
前端(:8080) → Nginx
               ├── /qa/runtime/* → Java(:8085) → Python(:8000)  ← SSE 透传
               ├── /qa/answer/*  → Java(:8085) → Python(:8000)  ← SSE 透传
               ├── /qa/*         → Java(:8085) → Python(:8000)  ← API 转发
               └── /charts/*    → Java(:8085)                   ← 图片代理
                                    ↓
                             Python(:8000)
                               ├── Ollama(:11434) Embedding
                               ├── ChromaDB(本地) 向量库
                             Java(:8085)
                               ├── MySQL 数据库
                               ├── Redis(:6379) 缓存
                               └── MinIO(:9000) 文件存储
```

```bash
# Docker 一键启动
docker compose up -d

# 或本地开发
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

---

## 技术栈

| 层级 | 技术 |
|------|------|
| 后端框架 | Python 3.11 + FastAPI + asyncio |
| AI 核心 | LangChain + DeepSeek API + Ollama Embeddings |
| 向量数据库 | ChromaDB |
| 缓存 | Redis |
| 关系数据库 | MySQL (Spring Boot + MyBatis) |
| 工具协议 | MCP (Model Context Protocol) |
| 实时推送 | SSE (Server-Sent Events) + RuntimeEventBus + Java SseEmitter |
| 文档存储 | MinIO |
| 前端 | 原生 HTML/CSS/JavaScript + EventSource |
| 容器化 | Docker Compose |

---

> 详细版本历史见本文档顶部版本路线图。

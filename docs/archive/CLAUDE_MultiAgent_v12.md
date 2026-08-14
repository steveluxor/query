# Query - Agent Runtime 架构 (v12: SSE 单流 + 并发隔离 + 可靠性加固)

## 系统架构

本项目是一个 **基于 Multi-Agent 的智能知识问答系统**，采用 RAG (Retrieval-Augmented Generation) 技术，通过 MCP (Model Context Protocol) 协议实现工具调用。

| 组件 | 路径 | 技术栈 | 端口 |
|------|------|--------|------|
| 前端 | `nginx/html/` | Nginx + 原生 HTML/CSS/JS | :8080 |
| Java 后端 | `java/` | Spring Boot + MyBatis | :8085 |
| Python AI 服务 | `app/` | FastAPI + LangChain + ChromaDB | :8000 |
| Stream Consumer | `app/stream_consumer.py` | Python + pika（RabbitMQ 消费者）| 无（独立容器）|

**请求流向：** 前端(:8080) → Nginx → Java(:8085) → Python(:8000)

**Python 对前端不可见**，所有通信经 Java 透传。

---

## 从 v11 到 v12 的变更

### 核心变更

| 变更 | v11 | v12 |
|------|-----|-----|
| SSE 流 | 双流：`/qa/runtime` + `/qa/answer` 独立连接 | **[v12: 修改]** 单流：token_chunk 合并进 `/qa/runtime`，删除 `/qa/answer` 端点 |
| SSE 心跳 | 无 | **[v12: 新增]** 双端心跳：Python `: ping` + Java SseEmitter comment，15s 间隔保活 |
| Java SSE 线程 | 公共 ForkJoinPool | **[v12: 修改]** 专用线程池（sseExecutor）+ 心跳调度器 + PreDestroy 清理 |
| Java SSE 状态码 | 直接把错误体当 SSE 转发 | **[v12: 修改]** 校验 Python 非 200 → 透传 runtime_error 事件 |
| callback 鉴权 | 无 | **[v12: 新增]** `X-Callback-Token` header 校验（Python 发送 / Java 验证）|
| callback 容错 | 失败静默 | **[v12: 修改]** callback 失败 → 发 `RUNTIME_ERROR`，不回 `RUNTIME_COMPLETED` |
| RUNTIME_COMPLETED 载荷 | 携带完整 result（含 base64 图表）| **[v12: 修改]** 精简为仅 `session_id`，完整结果走 callback 持久化 |
| RAG 上下文 | SearchContext + AnalysisContext 双结构 | **[v12: 修改]** 合并为单一 SearchContext，移除冗余配置 |
| 并行分支检索隔离 | 共享 `search_ctx` 被最后一次搜索覆盖 | **[v12: 新增]** 按 DAG 依赖锁定上游"检索提供者"（`ctx_source_id`）|
| 排名识别 | 仅阿拉伯数字（"第1高"）| **[v12: 修改]** 支持汉字序数词（"第二贵""第三名"）|
| 前端重复消息 | 回答完成后同一轮出现两遍 | **[v12: 修复]** `_finishQuestion` 先清 pending 再加载历史 |
| 会话列表排序 | 按 id 倒序 | **[v12: 修改]** 按 update_time（Java 每轮刷新 + 前端排序）|
| 会话切换 | 进行中 DAG/trace/回答丢失 | **[v12: 修复]** 切回时恢复实时过程视图，且不串台 |
| 手动停止 | 无 | **[v12: 新增]** `POST /qa/stop/{runId}` 全链路，先发取消事件再取消任务，半成品不持久化 |

### 架构升级路线

```
v1 (Multi-Agent 应用):
  Coordinator → Knowledge → Analysis → Generate → Critic 线性链
  AgentContext = 业务字段集合（evidence/sources/analysis/answer）
  Orchestrator 硬编码调度逻辑
  无 DAG、无校验、无 Planner

v2 (DAG 能力注册):
  AgentCapability: 输入输出 Schema 声明
  TaskGraph: DAG 数据结构（拓扑排序执行）
  AgentRegistry: 能力注册表（校验 Agent 可用性）
  Planner: LLM 生成 TaskGraph（简单模式仍走线性链）
  Orchestrator 读 Registry + 执行 TaskGraph

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

v12 (SSE 单流 + 并发隔离 + 可靠性加固):
  [v12: 修改] SSE 单流: token_chunk 合并进 /qa/runtime，删除 /qa/answer 端点
  [v12: 新增] 双端心跳: Python `: ping`（15s wait_for 超时）+ Java SseEmitter comment（15s 定时）
  [v12: 修改] Java SSE 专用线程池 + 心跳调度器 + @PreDestroy 清理
  [v12: 修改] Java SSE 校验 Python 状态码: 非 200 → 透传 runtime_error，不把错误体当 SSE
  [v12: 新增] callback 鉴权: X-Callback-Token（Java 校验 / Python 发送）
  [v12: 修改] callback 容错: 失败 → RUNTIME_ERROR，成功才 RUNTIME_COMPLETED
  [v12: 修改] RUNTIME_COMPLETED 精简载荷: 仅 session_id，完整结果走 callback 持久化
  [v12: 修改] RAG 简化: AnalysisContext 合并至 SearchContext，移除冗余配置
  [v12: 新增] 并行分支检索隔离: _resolve_search_provider（数据驱动）+ ctx_source_id + _get_search_ctx 三级查找
  [v12: 新增] _search_ctx_source_var: contextvars 隔离消费者工具的上游检索来源
  [v12: 修改] 排名识别: rank_patterns 支持汉字序数词（"第二贵""第三名"）
  [v12: 修改] EventBus: subscribe 未知 run 返回 None + 引用计数卸载 + gc 惰性清扫孤儿 run
  [v12: 修复] 前端重复消息: _finishQuestion 先清 pending 再 loadQaHistory
  [v12: 修改] 前端会话列表: 按 update_time 排序（Java update_title 每轮刷新 + 前端降序）
  [v12: 修复] 前端会话切换: _liveRunSessionId/_pendingSessionId 会话归属 + 切回恢复实时过程
  [v12: 修改] 前端渲染守卫: live trace/answer/DAG 仅渲染到所属会话，防串台
  [v12: 修改] LLM 默认超时 30s→120s: 慢 API 下消除 Planner 提前超时 + SDK 重试空转，Code 生成不再 Request timed out
  [v12: 修改] 搜索相关性过滤写回 search context: ctx.last_search_chunks 仅保留相关文档 + last_search_all_chunks 置空使惰性缓存失效，read_all_rows/calculate_* 只看到相关文档
  [v12: 修复] relevance.judge 模板花括号转义: {"relevant_ids":...} → {{...}}，修复 str.format KeyError
  [v12: 新增] Code prompt 数据范围守卫: 按 `文件` 字段只使用与 question 直接相关的文档数据，忽略无关文档（账单/清单等）
  [v12: 新增] 用户手动停止: POST /qa/stop/{runId} 全链路 + RUNTIME_CANCELLED 事件 + 先发事件后 cancel（避免 CancelledError 下再 await）+ 取消不持久化
```

---

## 为什么 Python 保持无状态

### "无状态"的确切含义

Python 不是零状态，而是**无持久化状态**——只有进程内瞬态，全部可丢弃、可重建：

| 状态 | 存放 | 生命周期 | 重建方式 |
|------|------|----------|----------|
| AgentMemory | 进程内 dict | 空闲 30min 淘汰 / LRU 上限 | Java 历史 → `rebuild_from_history` |
| RuntimeEventBus channel | 进程内 | run 结束即 cleanup | 不需要（瞬态）|
| MCP session search_ctx | 进程内 | run 结束即清理 | 不需要（瞬态）|

持久化 source of truth 在 Java 侧：MySQL（会话 / 对话历史）、Redis（历史缓存）、MinIO（图表文件）。Python 的 RedisStore 只读，写入由 Java 负责。

### 好处

1. **水平扩容**：无持久化状态 → 多实例部署无需会话粘滞 / 一致性哈希，任意请求可路由到任意实例
2. **故障恢复**：进程崩溃或重启无恢复负担，冷启动后从 Java 历史重建记忆，服务即刻可用
3. **发布升级零成本**：滚动重启不丢业务状态，AI 逻辑可频繁迭代上线
4. **职责边界清晰**：Java 管"持久化 + 鉴权"（企业级稳定性），Python 管"AI 计算"（快速演进），两者通过 SSE / HTTP callback / MCP 契约解耦，可独立演进
5. **规避分布式一致性问题**：无需跨实例共享 session、粘性路由、分布式锁

### 为什么 Java 做统一网关

前端只暴露 Java 一个入口，认证 / 权限 / 持久化统一收敛；Python 对前端不可见，AI 服务被隔离在安全边界内。Java 与 Python 通过契约解耦、独立扩容，任一侧升级不互相阻塞。

---

## SSE 单流实时回答 + 双端心跳

### 问题背景

v11 使用双流：`/qa/runtime`（Workflow 层状态）+ `/qa/answer`（token 增量）。token 独立流意味着：

- 前端同时维护两个 EventSource，生命周期与错误处理翻倍
- 回答流与状态流存在时序竞态（token 先到 / 状态后到）
- 两套 SSE 都要 Java 代理 + Nginx 透传，链路复杂

### 设计目标

- 单连接承载全部事件（状态 + token），前端只维护一个 EventSource
- 长 Planner / 无事件阶段连接不中断（心跳保活）
- Python 无状态，Java 稳定透传，链路可观测

### 为什么单流：双流 vs 单流对比

| 维度 | 双流 (v11) | 单流 (v12) |
|------|-----------|-----------|
| 连接数 | 2 条（runtime + answer 独立）| 1 条（runtime 承载全部事件）|
| 前端 | 2 个 EventSource，两套断连/重连/状态 | 1 个 EventSource + 事件分发表 |
| 心跳 | 每条流都要保活，否则代理断开 | 一处双端心跳（15s）|
| Java 侧 | 每 run 维护 2 个 SseEmitter + 2 个心跳调度 | 1 个，省线程池与调度资源 |
| 事件顺序 | 两路独立，存在跨流时序竞态 | 单通道内天然有序 |
| 失败面 | 一路断另一路还活，状态分裂 | 整体断，重连语义清晰 |

**核心原因**：token 本质上也是带 `type` 的事件，单独开一条 `/qa/answer` 流没有语义收益，只是把"一个 run 的状态"散落在两个连接上。单流用统一事件协议承载一切，前端只需维护一个连接生命周期。唯一成本是"单点流"风险——由双端心跳 + callback 容错兜底。

### 架构

```
POST /qa/ask → Java(:8085) → Python(:8000)
  → Python 立即返回 {run_id, session_id}
  → 后台 asyncio.create_task(_run_and_emit)

前端连接单条 SSE（经 Java 透传）:
  GET /qa/runtime/{run_id}  ← Nginx → Java SseEmitter → Python SSE
     ├── plan_generated / agent_started / agent_completed / agent_failed
     ├── token_chunk（打字机式实时回答）
     └── runtime_completed / runtime_error

Python 执行完成 → POST /qa/callback（X-Callback-Token 鉴权）→ Java 持久化到 MySQL/Redis/MinIO
```

### 心跳：为什么需要两端都发

| 环节 | 风险 | 心跳方案 |
|------|------|----------|
| Python → Java | 长 Planner 阶段无事件，`queue.get()` 阻塞 | `asyncio.wait_for(queue.get(), 15)` 超时后 `yield ": ping\n\n"` |
| Java → 前端 | 转发链路久无数据，nginx/浏览器可能断开 | `heartbeatScheduler.scheduleAtFixedRate(15s)` 发 `SseEmitter.event().comment("ping")` |

**Python 心跳**（`app/api/qa.py`）：

```python
while True:
    try:
        event = await asyncio.wait_for(queue.get(), timeout=15)
    except asyncio.TimeoutError:
        # SSE 心跳：长 Planner/无事件阶段保活（Java 代理不转发注释行，仅维持连接）
        yield ": ping\n\n"
        continue
    ...
```

**Java 心跳**（`java/.../QaServiceImpl.java`）：

```java
ScheduledFuture<?> heartbeat = heartbeatScheduler.scheduleAtFixedRate(() -> {
    try {
        emitter.send(SseEmitter.event().comment("ping"));
    } catch (Exception ignored) { /* 连接断开由 onCompletion 取消心跳 */ }
}, HEARTBEAT_INTERVAL_MS, HEARTBEAT_INTERVAL_MS, TimeUnit.MILLISECONDS);
```

**关键：** 注释行（`:` 开头 / comment）是 SSE 规范的保活手段，浏览器 EventSource 自动忽略，不产生事件。

### Java SSE 透传 + 可靠性

**文件**: `java/.../service/QaServiceImpl.java` — `proxySse()`

```java
private SseEmitter proxySse(String pythonUrl, String streamType, String runId) {
    SseEmitter emitter = new SseEmitter(300_000L); // 5 分钟超时

    // 心跳：15s 定时发 comment，避免长 Planner 阶段提前断开
    ScheduledFuture<?> heartbeat = heartbeatScheduler.scheduleAtFixedRate(...);

    sseExecutor.execute(() -> {
        HttpResponse<InputStream> response = httpClient.send(httpReq, BodyHandlers.ofInputStream());
        int statusCode = response.statusCode();
        if (statusCode != 200) {
            // 非 200：向前端透传 runtime_error，不把 Python 错误体当 SSE 转发
            emitter.send(SseEmitter.event().name("runtime_error").data(errorPayload));
            emitter.completeWithError(new RuntimeException(...));
            return;
        }
        try (BufferedReader reader = new BufferedReader(new InputStreamReader(response.body(), UTF_8))) {
            // 逐行解析 Python SSE（event:/data:/空行），保持 event name 转发
            ...
        }
        emitter.complete();
    });
    return emitter;
}
```

**可靠点：**
1. **专用线程池**（`Executors.newCachedThreadPool()`）：不占公共 ForkJoinPool，避免阻塞阻塞 Tomcat 其他请求
2. **心跳调度器**独立管理，`@PreDestroy` 统一 `shutdownNow()`
3. **状态码校验**：Python 非 200 时发 `runtime_error` 事件再结束，前端能感知失败而非无限挂起

### RuntimeEventBus — per-run pub-sub + 历史缓存 + 生命周期

**文件**: `app/core/runtime_event_bus.py`

```python
class RuntimeEventBus:
    """per-run pub-sub 事件总线
    设计要点：
    - 每个 run_id 多个 subscriber（runtime SSE + answer SSE 独立消费）
    - 历史缓存：迟到的 subscriber 可回放已发布事件
    - 预创建 channel：/qa/ask 时先 create_channel，避免事件丢失
    - subscribe 未知 run 返回 None：避免未知 run_id 永久挂起
    - 引用计数：unsubscribe 移除单个 queue，最后一个订阅者断开时删除 channel
    - gc 惰性清扫孤儿 run（ask 后从未连 SSE 的无主 run）
    - 无状态：run 结束后 cleanup，不持久化事件
    """
    def subscribe(self, run_id: str) -> asyncio.Queue | None:
        if run_id not in self._channels:
            return None
        q = asyncio.Queue()
        for event in self._history.get(run_id, []):   # 迟到回放
            q.put_nowait(event)
        self._channels[run_id].append(q)
        return q
```

**v12 强化：** 未知 run 返回 None（`/qa/runtime/{unknown}` 立即回 `runtime_error`，不再永久挂起）、引用计数卸载（只移除本订阅者，不误杀其他流）、孤儿 run 超时回收。

---

## callback 持久化鉴权容错

### 问题背景

v11 由 Python 在 `runtime_completed` 事件里携带完整 result 给前端，Java 只做透传；持久化依赖前端 reload 历史时同步。问题：

- callback 无鉴权：任意请求可伪造持久化
- callback 失败时前端仍收到 `runtime_completed`，形成"成功假象"
- `runtime_completed` 携带 base64 图表等大对象，SSE 载荷膨胀

### v12 方案

```
Python 执行完成
  → POST /qa/callback（Header: X-Callback-Token）→ Java 鉴权
  → 校验 token：不匹配 → 403
  → Java 持久化到 MySQL（QaHistory）+ Redis（历史缓存）+ MinIO（图表）
  → 成功 → Python 发 RUNTIME_COMPLETED（精简载荷，仅 session_id）
  → 失败 → Python 发 RUNTIME_ERROR，前端感知失败
```

**Python**（`app/api/qa.py`）：

```python
# 精简载荷：完整 result 已走 callback 持久化，前端 reload 历史即可
await context.emit(EventType.RUNTIME_COMPLETED, {"session_id": context.session_id})
```

```python
async def _callback_java(session_id, result, agent_memory) -> bool:
    headers = {}
    if settings.callback_token:
        headers["X-Callback-Token"] = settings.callback_token
    resp = await client.post(f"{java_base_url}/qa/callback",
                             json={"session_id": session_id, "result": result},
                             headers=headers)
    if resp.status_code == 200:
        agent_memory.mark_synced(session_id)   # 清除 dirty 标记
        return True
    return False   # 非 200 / 异常 → 调用方发 RUNTIME_ERROR
```

**Java**（`java/.../controller/QaController.java`）：

```java
@PostMapping("/callback")
public ResponseEntity<?> callback(@RequestBody Map<String, Object> body,
        @RequestHeader(value = "X-Callback-Token", defaultValue = "") String token) {
    if (!callbackToken.isBlank() && !callbackToken.equals(token)) {
        return ResponseEntity.status(HttpStatus.FORBIDDEN).body(Result.fail("callback token 校验失败"));
    }
    return ResponseEntity.ok(qaService.handleCallback(body));
}
```

---

## 用户手动停止

### 问题背景

Planner + 多 Agent DAG 一轮执行可能耗时 30s-150s（长 Planner、Extractor 并行提取、Generator 长文生成）。等待期间用户无法中断，只能干等。需要随时终止本次问答，且终止后不留半成品记录。

### 事件流

```
用户点「停止」
  → 前端 POST /qa/stop/{runId} (Java)
  → Java 转发 Python POST /qa/stop (body: {"run_id": runId})
  → Python: 先 publish RUNTIME_CANCELLED（所有 SSE 订阅者收到 → 流 break 结束）
  → 再 task.cancel()（中断后台 DAG 执行）
  → 取消的 run 不走 callback → 半成品不持久化
```

### 为什么先发取消事件、后取消任务

asyncio 下任务一旦处于取消态（cancelling），后续 `await` 会**立即重抛 CancelledError**。若先 `task.cancel()` 再 `await event_bus.publish(...)`，publish 永远不会完成。因此顺序必须是：先发布 `RUNTIME_CANCELLED` 让所有 SSE 订阅者退出，再 `task.cancel()`。

**Python**（`app/api/qa.py`）：

```python
@router.post("/stop")
async def stop_qa(req: StopRequest, event_bus: RuntimeEventBus = Depends(get_event_bus)):
    task = _active_tasks.get(req.run_id)
    if not task or task.done():
        return {"ok": False, "reason": "run not active"}
    # 1. 先发取消事件：所有 SSE 订阅者 break，流自然结束
    await event_bus.publish(RuntimeEvent(req.run_id, EventType.RUNTIME_CANCELLED, {"reason": "user_stopped"}))
    # 2. 再取消后台 DAG 任务（取消后不 emit completed/error，半成品不持久化）
    task.cancel()
    return {"ok": True}
```

SSE 流终止条件（`stream_runtime`）加入 `RUNTIME_CANCELLED`：

```python
if event.type in (EventType.RUNTIME_COMPLETED, EventType.RUNTIME_ERROR, EventType.RUNTIME_CANCELLED):
    break
```

### Java 不需要主动断流

Java 不维护 run→emitter 注册表。Python 取消后 SSE 流自然结束，`proxySse` 读线程读到 EOF → `emitter.complete()`。前端停止时已本地关闭 EventSource，Java 转发途中 `emitter.send` 失败会捕获并 break（`proxySse` 已内置容错），连接由 `onCompletion` 统一清理。

### 前端：按钮生命周期由 run 终结事件驱动

停止按钮在**整个 run 期间常显**（`_setQaRunning(true)`）。复位不放在 `handleSendQuestion` 的 `finally`——因为 `_connectSSE` 只是注册监听器就返回，此时 run 仍在执行。按钮复位统一由终结事件处理：

| 路径 | 复位 |
|------|------|
| runtime_completed / runtime_error / 连接断开兜底 | `_finishQuestion` → `_setQaRunning(false)` |
| runtime_cancelled / 用户点停止 | `_cleanupLiveRun` → `_setQaRunning(false)` |
| ask 请求本身失败 | catch 块 `_setQaRunning(false)` |

### 已知限制

- 同步 `llm.invoke()`（Planner / Chat）取消需等当前 LLM 调用返回才生效
- `CodeExecutor` 子进程仅在超时（TimeoutError）时 kill，取消时不一定立即回收

---

## 并行分支 DAG 检索上下文隔离

### 问题背景

消费者工具（`calculate_sum` / `calculate_rank` / `read_all_rows`）不自己搜索，依赖上游检索 Agent 产生的 SearchContext。并行分支 DAG 下：

```
            ┌─ Search1 ─→ Sum1 ─┐
Planner ──→ Extractor ─ Search2 ─→ Sum2 ─→ Generator
            └─ Search3 ─→ Rank1 ─┘
```

MCP session 的共享 `search_ctx` 会被**最后一次搜索覆盖**，导致 `Sum1` 可能读到 `Search3` 的数据 → 结果串台。

### 方案：按 DAG 依赖锁定上游"检索提供者"

**数据驱动**，不硬编码 agent 名：检索提供者 = capability.tools 包含 `search_documents` 的 task。

**Orchestrator**（`app/core/agent_orchestrator.py`）— 递归向上游找最近的检索提供者：

```python
def _resolve_search_provider(self, context, task) -> str:
    """找到最近的上游"检索提供者" task_id（capability.tools 含 search_documents）。
    数据驱动：通过 capability.tools 判断，不硬编码 agent 名。"""
    # 自身就是检索提供者（会 search_documents）→ 用自己的 ctx
    own_cap = self.registry.get(task.agent)
    if own_cap and "search_documents" in own_cap.tools:
        return ""
    # 递归向上游依赖链查找最近的检索提供者
    def _walk(tid):
        dep = task_map.get(tid)
        cap = self.registry.get(dep.agent)
        if cap and "search_documents" in cap.tools:
            return dep.id
        for up in dep.depends_on:
            if r := _walk(up): return r
    ...
```

执行 task 前写入 contextvar：

```python
_task_id_var.set(task.id)
_search_ctx_source_var.set(self._resolve_search_provider(context, task))
```

**MCP Client**（`app/core/mcp/client.py`）— 消费者工具注入 `ctx_source_id`：

```python
ctx_source = _search_ctx_source_var.get()
if ctx_source:
    arguments = {"ctx_source_id": ctx_source, **arguments}
```

**MCP Server**（`app/core/mcp/server.py`）— 三级查找：

```python
def _get_search_ctx(session, task_id, ctx_source_id=""):
    """1. ctx_source_id（消费者工具锁定的上游"检索提供者"）
       2. 自身 task_id 的搜索上下文
       3. 共享 search_ctx（串行链式场景）"""
    if ctx_source_id and session.search_contexts.get(ctx_source_id):
        return session.search_contexts[ctx_source_id]
    if task_id and session.search_contexts.get(task_id):
        return session.search_contexts[task_id]
    return session.search_ctx
```

**隔离原理：** 每个检索 task 写入 `session.search_contexts[task_id]`，消费者工具通过 `_search_ctx_source_var` 锁定自己上游的检索 task_id，只读那个分支的上下文——并行分支互不干扰。

---

## RAG 引擎简化

v11 遗留 SearchContext + AnalysisContext 双结构，且分析工具维护独立 `analysis_ctx`。v12 合并为单一 SearchContext：

- `AnalysisContext` 删除，`calculate_sum/rank/read_all_rows` 统一读取 SearchContext
- MCP session 删除 `analysis_ctx` 字段（`app/models/mcp_session.py`）
- `rag_engine.py` 大幅精简（约 130 行净减），冗余配置移除

---

## 前端可靠性加固

### 1. 重复消息修复

**根因**：`_finishQuestion` 先 `loadQaHistory()` 后清 pending 气泡，`renderHistoricalMessages` 把进行中的气泡再追加一次。

**修复**：清 pending 移到 `loadQaHistory()` 之前。

### 2. 会话列表按更新时间排序

- Java `updateTitle` SQL：`and title is null` 改为 `IFNULL(title, #{title})` → 标题仍取首问，但 `update_time` **每轮提问刷新**
- 前端 `renderSessionList` 按 `updateTime` 降序，缺失时回退 `id`，未持久化的新会话固定顶部

### 3. 会话切换恢复进行中过程视图

**问题**：提问后切走再切回，实时 DAG/Trace/流式回答丢失（全局 live 元素被 `innerHTML` 重写摘除，恢复是事件驱动，静默期不重插）。

**方案**（会话归属 + 切回恢复 + 防串台）：

```js
let _liveRunSessionId = null;   // 当前 SSE run 所属 session
let _livePlan = null;           // live DAG 的 plan（切回重绘用）
state._pendingSessionId = null; // 进行中气泡所属 session

// 渲染守卫：live 元素只渲染到所属会话，防串台
function _renderLiveTrace() {
    if (_liveRunSessionId !== state.currentSessionId) return;
    ...
}

// 切回时从内存态恢复实时进度
function _restoreLiveProcess() {
    if (_liveRunSessionId !== state.currentSessionId) return;
    if (!_currentRuntimeSource) return;
    if (_livePlan) _restoreLiveDag();
    if (_liveTraceSteps.length > 0) _renderLiveTrace();
    if (_liveAnswerText) _renderLiveAnswer();
}
```

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

### 5. SSE 单流 + 双端心跳 + Java 透传

```
前端 → Nginx → Java SseEmitter → Python SSE（单连接）
                                   ├── Workflow 事件（plan/agent 状态）
                                   └── token_chunk（打字机回答）
Python → Java /qa/callback（X-Callback-Token）→ 持久化
```

单连接承载全部事件；Python + Java 双端 15s 心跳保活；Java 校验状态码 + 专用线程池。

### 6. 并行分支 DAG 检索上下文隔离

消费者工具通过 `_resolve_search_provider` 按 DAG 依赖锁定上游检索提供者（数据驱动，capability.tools 判断），contextvars 隔离，并行分支互不串台。

### 7. CodeAgent 沙箱

进程隔离 + 资源限制（512MB + 60s）+ 模块白名单，降低 LLM 生成代码执行风险。

### 8. MCP 工具协议

标准化 Tool Interface 解耦 Runtime 与工具实现。

### 9. LLM 推理管线优化

Extractor 输出精简 → Generator/Critic prompt 自动瘦身 → Token Metrics 全链路追踪。

---

## 项目规模

| 指标 | 数值 |
|------|------|
| Python 代码 | ~6590 行 |
| Java 代码 | ~2528 行 |
| 前端代码 | ~4558 行 |
| 领域 Agent | 7 个（Retrieval / Extraction / Analysis / Code / Critic / Chat / Base）|
| MCP 工具 | 8 个（search / list / sum / rank / read_all / add / delete / set_ids）|
| DAG 校验层 | 6 层（Structure / Capability / Goal / DataFlow / Policy / Rule）|
| Docker 容器 | 9 个 |
| 数据存储 | MySQL + Redis + MinIO + ChromaDB |
| 消息队列 | RabbitMQ |
| SSE 端点 | 1 个（经 Java 透传，单流）|
| API 端点 | ~30 个（Java 27 + Python 5，含内部 callback）|

---

## 部署架构

```
前端(:8080) → Nginx
               ├── /qa/runtime/* → Java(:8085) → Python(:8000)  ← SSE 透传（单流）
               ├── /qa/*         → Java(:8085) → Python(:8000)  ← API 转发 + callback 鉴权
               └── /charts/*    → Java(:8085)                   ← 图片代理

基础设施:
  Python(:8000)
    ├── Ollama(:11434) Embedding
    └── ChromaDB（嵌入 python-ai 容器，共享 volume 持久化）
  Java(:8085)
    ├── MySQL(:3307) 数据库
    ├── Redis(:6379) 缓存
    └── MinIO(:9000) 文件存储
  RabbitMQ(:5672) 消息队列
    └── Stream Consumer 消费者（从队列取任务 → 下载文件 → 调 python-ai 向量化）
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
| 后端框架 | Python 3.12 + FastAPI + asyncio |
| AI 核心 | LangChain + DeepSeek API + Ollama Embeddings |
| 向量数据库 | ChromaDB（嵌入 python-ai 容器） |
| 缓存 | Redis |
| 关系数据库 | MySQL (Spring Boot + MyBatis) |
| 消息队列 | RabbitMQ（文档向量化异步任务） |
| 工具协议 | MCP (Model Context Protocol) |
| 实时推送 | SSE 单流 + RuntimeEventBus + Java SseEmitter + 双端心跳 |
| 文档存储 | MinIO |
| 前端 | 原生 HTML/CSS/JavaScript + EventSource |
| 容器化 | Docker Compose（9 个容器） |

---

> 详细版本历史见本文档顶部版本路线图。

# Query — 智能知识问答系统

> **LLM generates plans, Runtime guarantees execution.**
>
> 基于 Planner + DAG Workflow 的 Multi-Agent 自主任务执行系统，SSE 单流实时推送，Java 统一网关

---

## Demo

### 流程 1：数值分析 + 图表生成

**用户输入：** 统计各品牌的花费金额占比，生成饼图，并总结哪个品牌花费最高

**Planner 自动规划 DAG：**

```
              ┌─────────────┐
              │  Retrieval  │  ← 检索（单任务）
              └──────┬──────┘
                     │
        ┌────────────┴────────────┐
        │                         │
   ┌────┴─────┐             ┌─────┴────┐
   │ Analysis │             │ CodeAgent│  ← 并行执行（均依赖检索）
   └────┬─────┘             └─────┬────┘
        │                         │
        └────────────┬────────────┘
                     │
              ┌──────┴──────┐
              │  Generator  │  ← SSE 逐 token 流式输出
              └──────┬──────┘
                     │
              ┌──────┴──────┐
              │   Critic    │  ← RuleValidator + LLM 两级审核
              └─────────────┘
```

> 纯数值计算问题会被规则裁剪：问题含「最高」等数值关键词，`_post_process_plan` 规则 2 移除 extractor task（数值问题只需 analysis 结果即可回答）。

**执行结果（3 轮实测，各阶段取最短）：**

| Agent | 耗时 | 产出 |
|-------|------|------|
| Planner | 18.3s | DAG（extractor 被规则裁剪） |
| Retrieval | 2.7s | 140 chunks（账.xlsx 全量） |
| Analysis | 18.8s | 各品牌花费金额排名 |
| CodeAgent | 16.1s | 饼图 PNG |
| Generator | 2.8s | 分析报告 |
| Critic | 2.0s | RuleValidator 通过 + LLM 评分 8.0 |
| **总计** | **~45s** | 完整分析报告（Analysis ∥ CodeAgent 并行） |

### 流程 2：文档对比 + 知识抽取

**用户输入：** 对比按键中断、定时器控制、ARM 汇编这三份实验报告，分析各自的实验目的、所用硬件设备和程序实现思路，总结三份报告的差异

**Planner 自动规划 DAG：**

```
              ┌─────────────┐
              │  Retrieval  │  ← 检索（单任务）
              └──────┬──────┘
                     │
              ┌──────┴──────┐
              │  Extractor  │  ← Map-Reduce 知识抽取
              └──────┬──────┘
                     │
              ┌──────┴──────┐
              │  Generator  │  ← SSE 逐 token 流式输出
              └──────┬──────┘
                     │
              ┌──────┴──────┐
              │   Critic    │  ← RuleValidator + LLM 两级审核
              └─────────────┘
```

**执行结果（3 轮实测，各阶段取最短）：**

| Agent | 耗时 | 产出 |
|-------|------|------|
| Planner | 6.8s | 4 节点 DAG |
| Retrieval | 3.4s | 3 份实验报告全文 |
| Extractor | 46.7s | 22 个知识对象 / 12 条证据 |
| Generator | 16.5s | 对比分析报告 |
| Critic | 1.0s | RuleValidator 通过 + LLM 审核 |
| **总计** | **~74s** | 三份实验报告差异对比报告 |

> **以上为实测（N=3，各阶段取最短值）；总计为合成下界（非单轮实测）。** DeepSeek 高延迟下单轮实际可达 55-108s，波动较大。

---

## 为什么不是传统 RAG

传统 RAG 的核心问题是**执行流程固定**。当任务从"回答问题"扩展到"分析数据、生成代码、制作图表、验证结果"时，单一 Agent 难以维护。

| 维度 | 传统 RAG | 本系统 |
|------|----------|--------|
| 流程 | 固定：检索 → 生成 | 动态：Planner 生成 DAG |
| 执行 | 单 Agent 线性调用 | 多 Agent 并行 + 依赖调度 |
| 能力 | 检索 + 文本生成 | 检索 + 抽取 + 计算 + 代码执行 + 图表 |
| 质量 | 无审核机制 | 两级 Critic + 局部重试 |
| 扩展 | 改代码 | 注册 Agent + 写 prompt |

**传统 RAG 是 Retriever-centric，本系统是 Planner-centric。**

RAG 仍然作为 Capability 存在（Retrieval Agent），而不是整个系统范式。

---

## 系统架构

```
                      User Goal
                         │
                    ┌────┴────┐
                    │ Planner │  ← LLM 理解意图，生成 DAG
                    └────┬────┘
                         │
                 ┌───────┴───────┐
                 │  Agent Runtime │  ← 拓扑排序 + 并行执行 + 数据流注入
                 └───────┬───────┘
                         │
                   ┌─────┴─────┐
                   │ Retrieval │  ← MCP Tools
                   └─────┬─────┘
                         │
                   ┌─────┴─────┐
                   │ Extractor │  ← Map-Reduce 知识抽取（精简输出）
                   └─────┬─────┘
                         │
              ┌──────────┴──────────┐
              │                     │
        ┌─────┴─────┐         ┌─────┴─────┐
        │ Analysis  │         │ CodeAgent │  ← 并行执行
        └─────┬─────┘         └─────┬─────┘
              │                     │
              └──────────┬──────────┘
                         │
                   ┌─────┴─────┐
                   │ Generator │  ← SSE 逐 token 流式输出
                   └─────┬─────┘
                         │
                   ┌─────┴─────┐
                   │  Critic   │  ← RuleValidator(<100ms) + LLM(5-15s)
                   └───────────┘
```

| 层级 | 职责 |
|------|------|
| **Planning Layer** | 理解用户意图，生成任务 DAG |
| **Runtime Layer** | DAG 调度、并行执行、数据流注入 |
| **Capability Layer** | 检索、分析、代码执行等能力 |
| **Presentation Layer** | SSE 单流 + Java 透传 + 前端实时渲染 |

---

## Agent 输入输出

| Agent | Inputs | Outputs | 工具 |
|-------|--------|---------|------|
| **RetrievalAgent** | _(无端口声明，通过 `_task_objective_var` 获取任务目标)_ | `document_bundle`: DocumentBundle · `retrieval_report`: RetrievalReport | `search_documents` · `read_all_rows` |
| **AnalysisAgent** | _(无端口声明)_ | `analysis`: AnalysisResult | `calculate_sum` · `calculate_rank` |
| **ExtractionAgent** | `knowledge_document`: DocumentBundle _(required)_ | `knowledge_objects`: list[KnowledgeObject] · `evidence`: list[Evidence] · `sources`: list[dict] | _(无工具，纯 LLM Map-Reduce)_ |
| **CodeAgent** | `document_bundle`: DocumentBundle | `code_result`: CodeResult | _(无工具，沙箱执行 LLM 生成的代码)_ |
| **AnswerGenerator** | `structured_knowledge`: list[KnowledgeObject] · `evidence_list`: list[Evidence] · `source_meta`: list[dict] · `analysis_result`: AnalysisResult \| None · `code_result`: CodeResult \| None | `answer`: str _(SSE 流式输出)_ | _(无工具)_ |
| **CriticAgent** | `evidence_list`: list · `generated_answer`: str · `retrieval_report`: RetrievalReport · `analysis_result`: AnalysisResult \| None _(前三项 required)_ | `critique`: str · `need_retry`: bool · `retry_target`: str | _(无工具，RuleValidator + LLM 两级审核)_ |
| **ChatAgent** | _(无端口声明)_ | `answer`: str | _(无工具，纯 LLM 闲聊)_ |

> `required_inputs` 为空的端口视为可选。port_bindings 根据类型自动匹配上游输出，上游未产出可选端口时不影响执行。

---

## 数据结构

### 文档与检索

**DocumentChunk** — 文档切片，检索结果的最小单位

| 字段 | 类型 | 含义 |
|------|------|------|
| `source` | str | 文件名，如 `账.xlsx` |
| `content` | str | chunk 文本内容 |
| `chunk_index` | int | 在文档中的序号 |
| `total_chunks` | int | 该文档的总 chunk 数 |

**DocumentBundle** — 文档包，RetrievalAgent 的输出

| 字段 | 类型 | 含义 |
|------|------|------|
| `chunks` | list[DocumentChunk] | 检索到的所有文档切片 |

**RetrievalReport** — 检索完整性报告

| 字段 | 类型 | 含义 |
|------|------|------|
| `sources` | list[str] | 搜到的文档名列表 |
| `total_chunks` | int | 命中 chunk 总数 |
| `returned_chunks` | int | 实际返回数 |
| `is_complete` | bool | 是否已调 read_all_rows |
| `read_all_rows_called` | bool | 是否调了全量读取 |
| `searches_performed` | int | 搜索次数 |

### 知识抽取

**KnowledgeObject** — 结构化知识对象（Extractor 输出）

| 字段 | 类型 | 含义 |
|------|------|------|
| `topic` | str | 主题/实体名，如 "实验一"、"万代" |
| `attributes` | dict | 结构化属性（短语化，非完整句子） |
| `source` | str | 来源文档 |
| `confidence` | float | 提取置信度 0-1 |

**Evidence** — 事实证据（Extractor 输出）

| 字段 | 类型 | 含义 |
|------|------|------|
| `statement` | str | 核心断言，50-80 字，如 "2024年A产品销量70万" |
| `source` | str | 来源文档 |
| `evidence_type` | str | `"table"` / `"text"` / `"calculation"` |
| `metadata` | dict | 可选元数据，如 `{"sheet": "Sheet1", "row": 12}` |

### 分析与计算

**Calculation** — 单次计算结果

| 字段 | 类型 | 含义 |
|------|------|------|
| `operation` | str | `"sum"` / `"rank"` |
| `field` | str | 计算字段，如 `"price"` |
| `arguments` | dict | 过滤条件，如 `{"row_filter": "前10行"}` |
| `result` | Any | 计算结果值 |
| `source` | str | 来源文档 |

**AnalysisResult** — 结构化分析输出（AnalysisAgent 输出）

| 字段 | 类型 | 含义 |
|------|------|------|
| `calculations` | list[Calculation] | 计算列表 |
| `findings` | list[str] | 发现，如 "销量下降30%" |
| `conclusions` | list[str] | 结论，如 "供应链影响较大" |

**CodeResult** — 代码执行结果（CodeAgent 输出）

| 字段 | 类型 | 含义 |
|------|------|------|
| `code` | str | LLM 生成的 Python 代码 |
| `output` | Any | result 变量的值 |
| `stdout` | str | 标准输出 |
| `error` | str | 错误信息 |
| `success` | bool | 是否成功 |
| `execution_time_ms` | int | 执行耗时 |
| `retry_count` | int | 重试次数 |
| `image_paths` | list[str] | 图表本地路径（临时） |
| `image_data` | list[str] | base64 编码 PNG |

### 审核与追踪

**CriticResult** — 审核结果（Critic 内部使用）

| 字段 | 类型 | 含义 |
|------|------|------|
| `score` | int | 1-10 分 |
| `problems` | list[str] | 发现的问题 |
| `need_retry` | bool | 是否需要重试 |
| `retry_target` | str | 重试目标：`"retrieval"` / `"generator"` / `"all"` |

**AgentTrace** — 单个 Agent 的执行轨迹（前端 Agent Trace 逐步展开）

| 字段 | 类型 | 含义 |
|------|------|------|
| `task_id` | str | 关联 DAG 任务 ID |
| `agent` | str | Agent 名称 |
| `start_time` | str | 开始时间戳 |
| `end_time` | str | 结束时间戳 |
| `tools_called` | list[str] | 调用的工具列表 |
| `input_summary` | str | 输入摘要 |
| `output_summary` | str | 输出摘要 |

**AgentOutput** — 输出条目包装层（context.set_output 内部）

| 字段 | 类型 | 含义 |
|------|------|------|
| `value` | Any | 实际数据（上面任意类型） |
| `producer` | str | 生产者 Agent 名 |
| `version` | int | 写入次数（自动递增） |
| `timestamp` | float | 写入时间 |
| `metadata` | dict | 扩展元数据 |

### 数据流总览

```
DocumentChunk → DocumentBundle → Extractor → KnowledgeObject + Evidence
                                    ↓
                    AnalysisAgent → AnalysisResult ─┐
                    CodeAgent    → CodeResult    ──┤
                                                   ↓
                                    Generator → answer → Critic
```

---

## 核心设计亮点

### 1. 声明式 Agent Capability Contract

Orchestrator 不感知 Agent 内部逻辑，只根据 Capability（输入输出 Schema）自动完成调度。Agent 是声明式能力节点，新增 Agent 无需修改 Orchestrator。

### 2. DAG Runtime 六层校验

**LLM 决定"做什么"，Runtime 保证"怎么执行"。** LLM 输出不可直接执行，经六层校验 + 后处理兜底修正：

```
Planner Output → Schema Validation → Capability Validation → Graph Repair → Execution
```

### 3. port_bindings 数据流

通过声明式数据通道替代 Agent 间隐式共享状态，task 间通过 port_bindings 自动注入数据。

### 4. 两级 Critic 闭环控制

```
Answer → RuleValidator (确定性, <100ms) → 通过?
                                          ↓ 不通过 → 直接重试
                                Slim LLM Critic (5-15s, 矛盾检测)
```

RuleValidator 做空 answer + 数值一致性检查（支持 95/95%/0.95 normalize），LLM Critic 做语义矛盾检测。

### 5. SSE 单流 + Java 透传 + 双端心跳

```
POST /qa/ask → Java → Python → {run_id}           ← 异步返回
GET /qa/runtime/{run_id}  → Nginx → Java SseEmitter → Python SSE  ← 唯一流
POST /qa/callback          ← Python → Java 持久化（X-Callback-Token 鉴权）
```

**Python 对前端不可见**，所有通信经 Java 透传。token_chunk 与事件合并为**单条 SSE 流**（`/qa/runtime`），删除冗余的 `/qa/answer` 端点；RuntimeEventBus 历史缓存保证迟到订阅不丢事件。

- **双端心跳保活**：Python `: ping`（asyncio.wait_for 15s 超时）+ Java SseEmitter comment（15s 调度），规避 Nginx/proxy 空闲断连
- **callback 鉴权容错**：Python 携带 `X-Callback-Token` 调用 `/qa/callback`，Java 校验失败 → 发 `RUNTIME_ERROR` 不回 `RUNTIME_COMPLETED`；完成载荷精简为仅 `session_id`
- **并发隔离**：并行分支 DAG 按依赖锁定上游"检索提供者"（`ctx_source_id`），各任务读写独立 search context，不串台
- **手动停止**：回答过程中点「停止」→ `POST /qa/stop/{runId}` 经 Java 转发 Python，先发 `RUNTIME_CANCELLED`（SSE 流结束）再 `task.cancel()`；取消的 run 不持久化，无半成品记录

前端实时显示：DAG 节点状态变化 + Agent Trace 逐步展开 + 标签页切换查看完整结果；切换会话再切回可恢复进行中过程视图。

### 6. CodeAgent 沙箱

进程隔离 + 资源限制（512MB + 60s）+ 模块白名单，降低 LLM 生成代码执行风险。图片 base64 编码传回。

### 7. MCP 工具协议

标准化 Tool Interface 解耦 Runtime 与工具实现。支持工具独立部署、多语言实现。

### 8. LLM 推理管线优化

Extractor 输出精简（attributes 短语化 + evidence 核心断言 50-80 字）→ Generator/Critic prompt 自动瘦身 → Token Metrics 全链路追踪。

### 9. 并行分支 DAG 检索上下文隔离

并行分支各 task 通过 contextvars 隔离任务上下文，MCP 按 DAG 依赖锁定上游"检索提供者"（`ctx_source_id`），串行链式场景复用共享检索结果。**防御性保留**：当前 planner 只生成单检索 DAG，该隔离机制实际不触发，仅在出现多并行检索分支时保证互不覆盖、数据流正确注入。

### 10. 检索相关性过滤 + 数据范围收敛

MCP Server 层 LLM 批量判断文档摘要与问题相关性，过滤后**写回 search context**（`last_search_chunks` + 惰性缓存失效），`read_all_rows`/`calculate_*` 只看到相关文档；Code prompt 增加按 `文件` 字段的数据范围守卫。无关文档（如账单）不会全量泄漏给 analysis/code。

---

## 关键设计决策（为什么）

### 为什么 SSE 用单流而不是双流

token 本质上是带 `type` 的事件，独立开一条 `/qa/answer` 流没有语义收益，只会让连接、心跳、前端状态管理翻倍。单流下前端一个 EventSource、一处双端心跳、Java 只维护一个 SseEmitter，事件顺序天然一致。代价是单点风险，由双端心跳 + callback 容错兜底。

### 为什么 Python 保持无状态

Python 只有进程内瞬态（AgentMemory / 事件总线 / MCP session），无持久化状态；source of truth 在 Java 侧（MySQL / Redis / MinIO），Python 崩溃后从 Java 历史重建记忆。好处：多实例水平扩容无会话粘滞、崩溃重启零恢复成本、AI 逻辑可频繁发布、持久化与鉴权收敛在 Java 单一安全边界内。

### 为什么 Java 做统一网关

前端只暴露 Java 一个入口，认证 / 权限 / 持久化统一收敛，Python AI 服务对前端不可见。Java 与 Python 通过 SSE / HTTP callback / MCP 契约解耦，可独立演进。

### 为什么 LLM 默认超时用 120s

DeepSeek 高延迟下 30s 默认超时会让 SDK 自动重试 ×2，一次慢调用被拖成 ~90s 空转，且挂在链路第一个关键路径（Planner）上最明显。统一 `create_llm` 默认超时 120s，Planner/Code 等一次调用完成（~40s），前端感知显著下降；`@lru_cache` 重建容器即失效，无残留。

---

## 项目规模

| 指标 | 数值 |
|------|------|
| Python 代码 | ~6500 行 |
| Java 代码 | ~2000 行 |
| 前端代码 | ~2000 行 |
| 领域 Agent | 7 个 |
| MCP 工具 | 6 个 |
| DAG 校验层 | 6 层 |
| Docker 服务 | 9 个 |
| 数据库 | MySQL + Redis + ChromaDB |
| SSE 端点 | 1 个（Java 透传，单流）|
| API 端点 | 16 个 |

---

## 部署架构

```
前端(:8080) → Nginx
               ├── /qa/runtime/* → Java(:8085) → Python(:8000)  ← SSE 单流透传
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
| 实时推送 | SSE + RuntimeEventBus + Java SseEmitter |
| 文档存储 | MinIO |
| 前端 | 原生 HTML/CSS/JavaScript + EventSource |
| 容器化 | Docker Compose |

---

## 后续优化

1. **动态 Replanning**：根据执行中间结果动态调整任务图
2. **Agent 插件化**：热插拔 Agent 注册，支持自定义扩展

---

> 详细架构设计文档见 [docs/archive/CLAUDE_MultiAgent_v12.md](docs/archive/CLAUDE_MultiAgent_v12.md)

---

## 简历项目描述

**Query —— Planner + DAG Multi-Agent Execution Framework**

设计并实现 LLM 驱动的 Agent Workflow Runtime，将用户目标转换为 DAG TaskGraph，通过 Runtime 完成多 Agent 编排、并行执行和闭环质量控制。

- 设计声明式 Agent Capability Contract，通过输入输出 Schema 实现 Agent 解耦，支持 Agent 动态注册与能力扩展
- 实现 DAG Scheduler，支持拓扑排序、异步并行执行、port_bindings 数据流注入以及子图级失败恢复
- 构建 MCP Tool Runtime，通过标准化 Tool Interface 解耦 Agent 与 RAG、数据分析、代码执行等外部能力
- 实现两级 Critic 闭环：RuleValidator（确定性规则，<100ms）+ Slim LLM Critic（语义矛盾检测，5-15s），支持精准子图重执行
- 设计 SSE 单流 + Java 透传架构：Python 无状态，RuntimeEventBus per-run pub-sub + 历史缓存；双端心跳保活、callback 鉴权容错、并行分支检索上下文隔离，前端实时接收 DAG 状态和 Agent 执行进度
- 实现 CodeAgent Sandbox，通过进程隔离、资源限制和模块白名单降低 LLM 生成代码执行风险
- 优化 LLM 推理管线：Extractor 输出精简（attributes 短语化 + evidence 核心断言），Generator token budget，Critic slim prompt，全链路 Token Metrics
- 可靠性加固：全局 LLM 超时 120s 消除慢 API 重试空转；搜索相关性过滤写回上下文收敛下游数据范围；模板花括号转义防 KeyError
- 实现用户手动停止：回答过程中随时中断 DAG，先发取消事件（SSE 流干净结束）再取消 asyncio 任务，取消的 run 跳过持久化不产生半成品记录

# Query — 智能知识问答系统

> **LLM generates plans, Runtime guarantees execution.**
>
> 基于 Planner + DAG Workflow 的 Multi-Agent 自主任务执行系统，SSE 双流实时推送

---

## Demo

**用户输入：** 分析三个实验报告的差异，生成趋势图并总结结论

**Planner 自动规划 DAG：**

```
              ┌─────────────┐
              │  Retrieval  │  ← 并行检索
              └──┬───┬───┬──┘
                 │   │   │
             Search1 Search2 Search3  ← 3 个并行检索任务
                 │   │   │
                 └─┬─┴─┬─┘
                   │   │
            ┌──────┴───┴──────┐
            │    Extractor    │  ← Map-Reduce 知识抽取
            └────────┬────────┘
                     │
              ┌──────┴──────┐
         ┌────┴────┐  ┌─────┴─────┐
         │Analysis │  │ CodeAgent │  ← 并行执行
         └────┬────┘  └─────┬─────┘
              │             │
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

**执行结果：**

| Agent | 耗时 | 产出 |
|-------|------|------|
| Planner | 1.2s | 5 节点 DAG |
| Retrieval | 2.5s | 42 条证据 |
| Extractor | 8.4s | 42 个知识对象 |
| CodeAgent | 5.1s | 趋势图 PNG |
| Generator | 2.0s | 分析报告 |
| Critic | <2s | RuleValidator 通过 + LLM 评分 9.2 |
| **总计** | **~21s** | 完整多模态分析报告 |

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
| **Presentation Layer** | SSE 双流推送 + 前端实时渲染 |

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

### 5. SSE 双流架构

```
POST /qa/ask → {run_id}
GET /qa/runtime/{run_id}  → Agent 进度事件（低频）
GET /qa/answer/{run_id}   → token 增量流（高频，逐字渲染）
```

RuntimeEventBus per-run pub-sub，前端同时接收 Workflow 层状态和 Generation 层 token。

### 6. CodeAgent 沙箱

进程隔离 + 资源限制（512MB + 60s）+ 模块白名单，降低 LLM 生成代码执行风险。图片 base64 编码传回。

### 7. MCP 工具协议

标准化 Tool Interface 解耦 Runtime 与工具实现。支持工具独立部署、多语言实现。

### 8. LLM 推理管线优化

Extractor 输出精简（attributes 短语化 + evidence 核心断言 50-80 字）→ Generator/Critic prompt 自动瘦身 → Token Metrics 全链路追踪。

---

## 项目规模

| 指标 | 数值 |
|------|------|
| Python 代码 | ~4500 行 |
| TaskGraph Engine | 1 套 |
| 领域 Agent | 7 个 |
| MCP 工具 | 6 个 |
| DAG 校验层 | 6 层 |
| Docker 服务 | 8 个 |
| 数据库 | MySQL + Redis + ChromaDB |
| SSE 端点 | 2 个 |
| API 端点 | 13 个 |

---

## 部署架构

```
前端(:8080) → Nginx → Java(:8085) → Python(:8000)
                                      ↓
                              Ollama(:11434) Embedding
                              Redis(:6379) 缓存
                              ChromaDB(本地) 向量库
                              MinIO(:9000) 文件存储
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
| 实时推送 | SSE (Server-Sent Events) + RuntimeEventBus |
| 文档存储 | MinIO |
| 前端 | 原生 HTML/CSS/JavaScript + EventSource |
| 容器化 | Docker Compose |

---

## 后续优化

1. **动态 Replanning**：根据执行中间结果动态调整任务图
2. **Agent 插件化**：热插拔 Agent 注册，支持自定义扩展

---

> 详细架构设计文档见 `docs/archive/`

---

## 简历项目描述

**Query —— Planner + DAG Multi-Agent Execution Framework**

设计并实现 LLM 驱动的 Agent Workflow Runtime，将用户目标转换为 DAG TaskGraph，通过 Runtime 完成多 Agent 编排、并行执行和闭环质量控制。

- 设计声明式 Agent Capability Contract，通过输入输出 Schema 实现 Agent 解耦，支持 Agent 动态注册与能力扩展
- 实现 DAG Scheduler，支持拓扑排序、异步并行执行、port_bindings 数据流注入以及子图级失败恢复
- 构建 MCP Tool Runtime，通过标准化 Tool Interface 解耦 Agent 与 RAG、数据分析、代码执行等外部能力
- 实现两级 Critic 闭环：RuleValidator（确定性规则，<100ms）+ Slim LLM Critic（语义矛盾检测，5-15s），支持精准子图重执行
- 设计 SSE 双流架构：RuntimeEventBus per-run pub-sub，前端实时接收 Agent 进度和 token 增量流
- 实现 CodeAgent Sandbox，通过进程隔离、资源限制和模块白名单降低 LLM 生成代码执行风险
- 优化 LLM 推理管线：Extractor 输出精简（attributes 短语化 + evidence 核心断言），Generator token budget，Critic slim prompt，全链路 Token Metrics

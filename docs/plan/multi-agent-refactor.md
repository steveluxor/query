# Multi-Agent 信息流重构计划（定稿版 v3）

## Context

当前系统有两个核心问题：
1. Knowledge Agent 和 Analysis Agent 都生成 `context.answer`，职责重叠
2. MCP Tool 返回值中包含工作流控制指令，Tool 承担了 Agent 的决策职责

目标：建立清晰的信息流 `Question → Evidence → Inference → Answer`，每个 Agent 只产出自己该产出的信息类型。

## 重构后的数据流

```
Question
  │
  ▼
Coordinator
  │
  ▼
Knowledge Agent
  │  tools: search_documents, read_all_rows
  │  内部: ReAct tool loop → raw_tool_results → Evidence Extractor
  ▼
Evidence[]
  │
  ▼
Analysis Agent
  │  tools: calculate_sum, calculate_rank
  ▼
AnalysisResult
  │
  ▼
AnswerGenerator (独立模块)
  │
  ▼
Answer
  │
  ▼
Critic → CriticResult (need_retry + retry_target)
```

## 数据结构定义

### Evidence

```python
@dataclass
class Evidence:
    statement: str          # "2024年A产品销量70万"
    source: str             # "sales.xlsx"
    evidence_type: str      # "table" / "text" / "calculation"
    metadata: dict          # {"sheet": "Sheet1", "row": 12}
```

不使用 confidence。Chroma score（检索相似度）和 LLM confidence（生成判断）不应混在 Evidence 里。

### Calculation

```python
@dataclass
class Calculation:
    operation: str          # "sum" / "rank"
    field: str              # "price"
    arguments: dict         # {"row_filter": "前10行", "content_filter": "品牌=万代"}
    result: Any             # 5000
    source: str             # "sales.xlsx"
```

加入 arguments 以支持 Critic 验证和未来扩展（average, max, percentage_change）。

### AnalysisResult

```python
@dataclass
class AnalysisResult:
    calculations: list[Calculation]
    findings: list[str]     # ["销量下降30%"]
    conclusions: list[str]  # ["供应链影响较大"]
```

Analysis Agent 必须通过 Tool 获取计算结果，不允许 LLM 自行编造数值。

### CriticResult

```python
@dataclass
class CriticResult:
    score: int              # 1-10
    problems: list[str]     # ["缺少来源", ...]
    need_retry: bool        # 是否要求重新执行
    retry_target: str       # "knowledge" / "analysis" / "generator" / "all"
```

retry_target 控制重试范围，避免不必要的全量重跑。

### AgentTrace

```python
@dataclass
class AgentTrace:
    agent: str              # "Knowledge" / "Analysis" / "Generator" / "Critic"
    start_time: str
    end_time: str
    tools_called: list[str]
    input_summary: str      # 输入摘要
    output_summary: str     # 输出摘要（如 "generated 5 evidence"）
```

用于 Debug、展示 Multi-Agent 效果、后期优化成本分析。

### AgentContext 字段契约（禁止覆盖）

```python
@dataclass
class AgentContext:
    # 输入
    question: str
    history: list
    preferences: dict

    # Coordinator 写入
    plan: list[dict] | None = None

    # Knowledge 写入（只写一次，重复写入抛异常）
    evidence: list[Evidence] = field(default_factory=list)
    sources: list[dict] = field(default_factory=list)

    # Analysis 写入（只写一次）
    analysis: AnalysisResult | None = None

    # AnswerGenerator 写入
    answer: str = ""

    # Critic 写入
    critique: str = ""
    need_retry: bool = False
    retry_target: str = "all"

    # 执行轨迹
    traces: list[AgentTrace] = field(default_factory=list)
```

每个 Agent 通过 setter 方法写入，防止多个 Agent 修改同一个状态：

```python
def set_evidence(self, evidence: list[Evidence]):
    if self.evidence:
        raise RuntimeError("Evidence already exists — only Knowledge Agent can write this")
    self.evidence = evidence
```

## 修改文件清单

### 1. 新增 `app/models/data_types.py`
- 定义 Evidence, Calculation, AnalysisResult, CriticResult, AgentTrace dataclass

### 2. `app/models/schemas.py`
- Source 保持不变
- 删除旧 AnswerResponse 中直接暴露 answer 的逻辑

### 3. `app/core/agent_context.py`
- 重写为带 setter 方法的 dataclass，实现禁止覆盖
- 新增 evidence, analysis, need_retry, retry_target, traces 字段

### 4. `app/core/agents/knowledge_agent.py`
- tools: 只保留 search_documents, read_all_rows
- 保存 raw_tool_results（ReAct tool loop 的原始结果）
- 独立 extract_evidence() 方法：从 raw_tool_results 中提取 Evidence
- 使用 LangChain `with_structured_output(EvidenceSchema)` 强制结构化输出
- 通过 `context.set_evidence()` 写入

### 5. `app/core/agents/analysis_agent.py`
- tools: 只保留 calculate_sum, calculate_rank
- 使用 LangChain `with_structured_output(AnalysisSchema)` 强制结构化输出
- 通过 `context.set_analysis()` 写入

### 6. 新增 `app/core/generator/answer_generator.py`
- `AnswerGenerator` 类，`generate(context)` 方法
- prompt 只包含 question + evidence + analysis + sources
- 不包含任何 tool/search 实现细节
- 调 LLM 生成最终自然语言回答
- 通过 `context.set_answer()` 写入

### 7. `app/core/agent_orchestrator.py`
- 导入 AnswerGenerator，调用 `answer_generator.generate(context)`
- 简单模式：Knowledge → (Analysis 可选) → Generate → Critic
- 规划模式：逐步执行 → Generate → Critic
- Critic 重试：根据 retry_target 决定重跑范围
  - "knowledge" → 重跑 Knowledge + Generate
  - "analysis" → 重跑 Analysis + Generate
  - "generator" → 只重跑 Generate
  - "all" → 全部重跑

### 8. `app/core/agents/critic_agent.py`
- system prompt 修改：输入 question + evidence + analysis + answer
- 输出 CriticResult JSON（score, problems, need_retry, retry_target）
- 通过 `context.set_critique()` 写入

### 9. `app/mcp_server.py`
- search_documents 返回值改为结构化 JSON：
  ```json
  {
    "rows_returned": 20,
    "rows_total": 100,
    "is_complete": false,
    "available_actions": ["read_all_rows"],
    "data": "格式化的文档内容..."
  }
  ```
- 去掉强制指令，改为中性的 available_actions

### 10. `app/prompts.yaml`
- Knowledge: 只负责检索和提取事实，输出 JSON Evidence
- Analysis: 只负责计算，输出 JSON AnalysisResult
- Generator: 只包含 question/evidence/analysis/sources
- Critic: 输入 question/evidence/analysis/answer，输出 CriticResult

### 11. `app/core/agents/coordinator_agent.py`
- needs_analysis 描述修改

### 12. `app/main.py`
- 新增 AnswerGenerator 实例化，注入 Orchestrator

## 不改动的部分

- `rag_engine.py` — 搜索/计算逻辑不变
- `vector_store.py` — 不变
- `agent_memory.py` — 不变
- `mcp_tools.py` — 不变
- `mcp_client.py` — 不变

## 验证方式

1. 启动服务：`uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload`
2. 测试检索问答：确认 Knowledge 输出 evidence（结构化 JSON）而非 answer
3. 测试计算问答：确认 Analysis 输出 analysis（结构化 JSON）而非 answer
4. 测试最终答案：确认由 AnswerGenerator 统一生成
5. 测试 Critic：确认输出 need_retry + retry_target
6. 测试 MCP Tool：确认 search_documents 返回 available_actions
7. 测试 AgentTrace：确认执行轨迹被正确记录

# DAG 结构校验流程说明

## 背景

当前 DAG（有向无环图）结构校验的全链路流程，从 Planner 生成 TaskGraph 到最终执行前的校验过程。

---

## 整体流程

```
Planner LLM 生成 JSON
    ↓
_parse_task_graph() 解析为 TaskGraph 对象
    ↓
_validate_task_graph() 统一校验入口
    ├── WorkflowValidator.validate_structure()     — 结构合法性
    ├── AgentRegistry.validate_capabilities()       — Agent 注册 + 输出冲突
    ├── PolicyValidator.validate_controller_usage() — Controller 策略
    ├── GoalValidator.validate_goal_capability()    — 目标可达性（第一层）
    ├── GoalValidator.validate_goal_reachability()  — 目标可达性（第二层）
    └── DAGDataFlowValidator.validate_input_mapping() — 数据流校验
    ↓
通过 → 进入 _execute_plan() 执行
失败 → 记录警告，回退到 _fallback_plan() 线性 DAG
```

入口在 `app/core/agent_orchestrator.py:298` `_validate_task_graph()`。

---

## 第一关：WorkflowValidator.validate_structure() — 结构合法性

文件: `app/core/workflow_validator.py:11`

### 检查项

**① 空图检查**
- tasks 列表为空 → 拒绝

**② 依赖存在性检查**
- 遍历所有 TaskNode 的 `depends_on`，检查引用的 ID 是否存在于 tasks 列表中
- 不存在的依赖被记录为 `invalid_deps`，同时跳过循环检测以免误报

**③ 循环检测 — Kahn 拓扑排序**
- 构建邻接表（排除 invalid_deps）
- 计算每个节点的入度
- 从入度为 0 的节点开始 BFS 出队
- 最终 visited != total_tasks → 存在环 → 拒绝

### get_layers() — 分层计算

- 按 `depends_on` 关系为每个 task 计算深度层
- 迭代松弛法：`depth[child] = max(depth[child], depth[parent] + 1)`
- 返回 `{task_id: depth}` 字典（根节点 = 0）

---

## 第二关：AgentRegistry.validate_capabilities() — Agent 能力合法性

文件: `app/core/agent_registry.py:63`

### 检查项

**① Agent 注册检查**
- plan 中每个 task 的 agent 名称必须在 registry 中注册过
- 未注册 → 报错

**② 输出冲突检测**
- 按 layer 分组，检查同一层多个 task 是否写入相同的 `output_key`
- **仅警告**（`logger.warning`），不拒绝

**③ 控制动作契约校验**
- Planner 不应在 task 中指定 `action` / `control_action` / `retry_target`
- 这些应由 Controller 运行时决定

---

## 第三关：PolicyValidator.validate_controller_usage() — Controller 策略

文件: `app/core/workflow_validator.py:70`

### 检查项

- Role 为 `CONTROLLER` 的 Agent 不能是 DAG 根节点（无上游依赖）
- 除非 `AgentCapability.allow_root_controller = True`
- 屏蔽了 Controller 作为独立入口点的非法组合

---

## 第四关：GoalValidator — 目标可达性（两层）

文件: `app/core/workflow_validator.py:96`

### 第一层：validate_goal_capability()

- 遍历所有已注册 Agent 的 output_keys 全集
- 检查 `goal_outputs` 中的每个 key 至少有一个 Agent 能产出
- 纯注册表检查，不依赖 DAG 结构

### 第二层：validate_goal_reachability()

- 按 DAG 拓扑层传播 outputs
- 每层 task 的 output = 其 Agent 注册的 output_keys
- 汇总所有 task outputs 后检查是否能覆盖 `goal_outputs`
- 比第一层更严格：即使系统中有某个 Agent 能产出，但如果 Planner 没有在 DAG 中使用它 → 不可达

---

## 第五关：DAGDataFlowValidator.validate_input_mapping() — 数据流校验

文件: `app/core/workflow_validator.py:156`

### 检查项

**① 格式校验**
- `input_mapping` 值必须包含 `.` 分隔符（`task_id.output_key` 格式）
- 缺少 `.` → 报错

**② 源 task 存在性**
- `source_task_id` 必须在 plan 的 tasks 列表中

**③ 上游关系验证**
- 源 task 必须是当前 task 的直接依赖（`depends_on`）或传递上游
- 使用 `_is_ancestor()` BFS 向上遍历 `depends_on` 链

**④ output key 存在性**
- 引用的 output_key 必须在源 Agent 的注册 `output_keys` 中

---

## 校验失败的处理

文件: `app/core/agent_orchestrator.py:298-310`

- `_validate_task_graph()` 收集所有验证器的错误
- 有错误 → 记录 `logger.warning`，返回 `False`
- 调用方 `_plan()` 发现 `False` → 抛出 `PlannerError`
- `run()` 捕获 `PlannerError` → 调用 `_fallback_plan()` 生成线性 DAG（retrieval → extractor → generator）

---

## 执行时的拓扑保障

文件: `app/core/agent_orchestrator.py:98-141`

即使验证通过，`_execute_plan()` 仍用循环确保拓扑序执行：
- 每次迭代查找所有 `depends_on` 已完成（非 pending）的 task
- 用 `asyncio.gather` 并行执行同层无依赖的 task
- 如果没有 ready task 但仍有 pending task → 记录"依赖环或无效依赖"警告并跳出

---

## 涉及的源文件

| 文件 | 关键内容 |
|------|----------|
| `app/models/task_graph.py` | TaskNode, TaskGraph, get_descendants(), invalidate_subgraph() |
| `app/core/workflow_validator.py` | WorkflowValidator, PolicyValidator, GoalValidator, DAGDataFlowValidator |
| `app/core/agent_registry.py` | AgentRegistry.validate_capabilities(), create_default_registry() |
| `app/models/capability.py` | AgentCapability, AgentRole |
| `app/core/agent_orchestrator.py` | _validate_task_graph(), _execute_plan(), _plan(), _fallback_plan() |
| `app/exceptions.py` | WorkflowValidationError, WorkflowExecutionError, PlannerError |

---

## 验证方式

```bash
cd D:\DOWNLOAD\pycharm\query
.venv\Scripts\activate
python -m pytest tests/test_workflow_validator.py -v
python -m pytest tests/test_agent_registry.py -v
python -m pytest tests/test_orchestrator_integration.py -v -k "test_dag"
```

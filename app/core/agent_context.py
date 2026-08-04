from __future__ import annotations

import contextvars
import time
from dataclasses import dataclass, field
from threading import RLock

from app.models.data_types import AgentOutput, AgentTrace
from app.models.task_graph import TaskGraph

# asyncio-task-local task_id，用于 asyncio.gather 并发时隔离各 task 的 current_task_id
# 每个 asyncio Task 有独立的 Context 副本，set() 只影响当前 Task
_task_id_var: contextvars.ContextVar[str] = contextvars.ContextVar('agent_task_id', default='')
# 消费者工具（calculate_sum/rank/read_all_rows）读取 search ctx 的来源 task_id：
# 由 Orchestrator 按 DAG 依赖解析（上游"检索提供者"），空则用自身 task_id / 共享 search_ctx
# （防御性：当前 planner 单检索 DAG 下恒为唯一检索的 task_id，见 _resolve_search_provider）
_search_ctx_source_var: contextvars.ContextVar[str] = contextvars.ContextVar('search_ctx_source', default='')
# 子任务 objective：并行分支各 task 经 contextvar 隔离（与 _task_id_var 同机制），
# 共享字段 context.question 不再被覆盖，恒为用户原始问题
_task_objective_var: contextvars.ContextVar[str] = contextvars.ContextVar('agent_task_objective', default='')


@dataclass
class AgentStep:
    """单个 Agent 执行记录（base_agent.py 使用）"""
    name: str
    duration_ms: int
    summary: str


@dataclass
class AgentContext:
    """Agent 间共享上下文 — 系统字段 + Agent 数据交换容器

    职责边界：
      - 系统字段（question, session_id, plan 等）：初始化后只读
      - outputs（evidence, analysis, answer 等）：Agent 间数据交换，由 Capability 声明
      - outputs 的读写由 _lock 保护（线程安全）
    """

    # ==================== 系统字段（初始化后只读） ====================
    question: str
    session_id: str | None = None
    mcp_session_id: str = ""
    document_ids: list[int] | None = None
    history: list[dict] | None = None
    memory_context: str | None = None
    preferences: dict | None = None
    plan: TaskGraph | None = None

    # ==================== SSE Streaming ====================
    run_id: str = ""
    event_bus: object | None = None  # RuntimeEventBus（避免循环导入，用 object）

    # ==================== Agent 数据交换容器 ====================
    # key -> {task_id: AgentOutput} — 每个 output key 可被多个 task 写入
    outputs: dict[str, dict[str, AgentOutput]] = field(default_factory=dict)

    # ==================== 执行轨迹 ====================
    traces: list[AgentTrace] = field(default_factory=list)
    steps: list[AgentStep] = field(default_factory=list)
    start_time: float = field(default_factory=time.time)

    # ==================== 运行时上下文（每轮执行前设置） ====================
    current_task_id: str = ""
    merge_policies: dict[str, str] = field(default_factory=dict)
    dedup_key_funcs: dict[str, callable] = field(default_factory=dict)

    # ==================== 兼容字段（过渡期保留） ====================
    tools_called: list[str] = field(default_factory=list)
    is_agg: bool = False

    # ==================== 线程安全 ====================
    _lock: RLock = field(default_factory=RLock, repr=False, compare=False)

    # ==================== Agent 输出管理 ====================

    def set_output(self, key: str, value, producer: str = ""):
        """线程安全地设置输出，按 task_id 隔离存储（contextvar 优先）"""
        task_id = _task_id_var.get() or self.current_task_id
        with self._lock:
            entries = self.outputs.setdefault(key, {})
            entries[task_id] = AgentOutput(
                value=value,
                producer=producer or "",
                version=len(entries) + 1,
                timestamp=time.time(),
                metadata={"task_id": task_id},
            )

    def get_output(self, key: str, default=None, merge_policy: str = ""):
        """获取输出值 — 同一 key 被多 task 写入时按 merge_policy 合并

        merge_policy 优先级：显式传入 > self.merge_policies[key] > "append"
        """
        entries = self.outputs.get(key)
        if not entries:
            return default
        values = [e.value for e in entries.values()]
        if len(values) == 1:
            return values[0]

        if not merge_policy:
            merge_policy = self.merge_policies.get(key, "append")
        return self._merge_values(values, merge_policy, key)

    def _merge_values(self, values: list, policy: str, key: str):
        """通用合并策略 — replace / append / dedup"""
        if policy == "replace":
            return values[-1]

        # DocumentBundle 合并（按 chunks 合并）
        if all(hasattr(v, 'chunks') for v in values):
            merged_chunks = []
            for v in values:
                merged_chunks.extend(v.chunks)
            if policy == "dedup":
                seen = set()
                result = []
                for c in merged_chunks:
                    dk = (c.source, c.content[:200])
                    if dk not in seen:
                        seen.add(dk)
                        result.append(c)
                merged_chunks = result
            from app.models.data_types import DocumentBundle
            return DocumentBundle(chunks=merged_chunks)

        if isinstance(values[0], list):
            merged = []
            for v in values:
                if isinstance(v, list):
                    merged.extend(v)

            if policy == "dedup":
                dedup_func = self.dedup_key_funcs.get(key)
                seen = set()
                result = []
                for item in merged:
                    dk = dedup_func(item) if dedup_func else (repr(item)[:200],)
                    if dk not in seen:
                        seen.add(dk)
                        result.append(item)
                return result
            return merged

        return values[-1]

    def get_output_entry(self, key: str, task_id: str = "") -> AgentOutput | None:
        """获取完整 AgentOutput（含 producer/version/timestamp 元数据）"""
        entries = self.outputs.get(key)
        if not entries:
            return None
        if task_id:
            return entries.get(task_id)
        return list(entries.values())[-1]

    def clear_outputs(self, keys: list[str]):
        """按 key 列表清空 output（线程安全）"""
        with self._lock:
            for key in keys:
                self.outputs.pop(key, None)

    def has_output(self, key: str) -> bool:
        """检查指定 output 是否存在"""
        return key in self.outputs

    # ==================== 快捷方法（保留兼容） ====================

    def add_trace(self, trace: AgentTrace):
        self.traces.append(trace)

    # ==================== SSE 事件发射 ====================

    async def emit(self, event_type, data: dict | None = None):
        """便捷发射 SSE 事件（仅在 event_bus 存在时生效）"""
        if self.event_bus and self.run_id:
            from app.core.runtime_event_bus import RuntimeEvent
            await self.event_bus.publish(RuntimeEvent(
                run_id=self.run_id,
                type=event_type,
                data=data or {},
            ))

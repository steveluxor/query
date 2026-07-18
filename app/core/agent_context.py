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
        return AgentContext._merge_values(values, merge_policy, key)

    @staticmethod
    def _merge_values(values: list, policy: str, key: str):
        """通用合并策略 — replace / append / dedup"""
        if policy == "replace":
            return values[-1]

        if isinstance(values[0], list):
            merged = []
            for v in values:
                if isinstance(v, list):
                    merged.extend(v)

            if policy == "dedup":
                seen = set()
                result = []
                for item in merged:
                    dk = AgentContext._dedup_key(item, key)
                    if dk not in seen:
                        seen.add(dk)
                        result.append(item)
                return result
            return merged

        return values[-1]

    @staticmethod
    def _dedup_key(item, output_key: str) -> tuple:
        if output_key == "evidence":
            return (getattr(item, 'source', ''), getattr(item, 'statement', '')[:200])
        elif output_key == "sources":
            return ((item.get("file_name", "") if isinstance(item, dict) else ""), str(item)[:200])
        else:
            return (repr(item)[:200],)

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

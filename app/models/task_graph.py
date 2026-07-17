from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field


@dataclass
class TaskNode:
    """单个执行任务"""
    id: str                      # "task1"
    agent: str                   # "knowledge" / "analysis"
    objective: str               # "获取2024销售数据"
    depends_on: list[str] = field(default_factory=list)
    output_key: str = ""         # 本任务输出标识，如 "sales_data"
    status: str = "pending"      # pending / running / completed / failed / skipped


@dataclass
class TaskGraph:
    """Planner 输出的任务图"""
    goal: str                    # "分析销售下降原因"
    tasks: list[TaskNode] = field(default_factory=list)

    def get_descendants(self, task_id: str) -> set[str]:
        """BFS 查找所有下游依赖（包含自身）"""
        task_map = {t.id: t for t in self.tasks}
        if task_id not in task_map:
            return set()

        downstream = set()
        queue = deque([task_id])
        while queue:
            tid = queue.popleft()
            if tid in downstream:
                continue
            downstream.add(tid)
            for t in self.tasks:
                if tid in t.depends_on and t.id not in downstream:
                    queue.append(t.id)
        return downstream

    def invalidate_subgraph(self, task_ids: set[str]) -> set[str]:
        """将指定 task 及其所有下游标记为 pending，返回受影响的所有 task id"""
        affected = set()
        for tid in task_ids:
            affected |= self.get_descendants(tid)

        task_map = {t.id: t for t in self.tasks}
        for tid in affected:
            node = task_map.get(tid)
            if node:
                node.status = "pending"

        return affected

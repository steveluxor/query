from __future__ import annotations

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

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class ControlAction:
    """Runtime 控制信号 — Controller Agent 输出，Runtime 执行

    action_type: 控制类型（retry / terminate / pause 等）
    target_task_id: retry 时指定重跑的目标 task
    payload: 附加参数
    """
    action_type: str                          # "retry" / "terminate" / "pause"
    target_task_id: str | None = None         # retry 目标
    payload: dict = field(default_factory=dict)

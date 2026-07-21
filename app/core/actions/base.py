from __future__ import annotations

from abc import ABC, abstractmethod

from app.models.control import ControlAction


class ActionHandler(ABC):
    """ControlAction 处理器基类"""

    @property
    @abstractmethod
    def action_type(self) -> str:
        """处理的 ControlAction 类型（如 "retry" / "terminate"）"""
        ...

    @abstractmethod
    async def execute(self, action: ControlAction, context, orchestrator) -> None:
        """执行 ControlAction"""
        ...

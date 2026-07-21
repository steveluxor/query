from __future__ import annotations

import logging

from app.core.actions.base import ActionHandler
from app.core.actions.retry import RetryHandler
from app.core.actions.terminate import TerminateHandler
from app.exceptions import ControlActionError

logger = logging.getLogger(__name__)


class ActionRegistry:
    """ControlAction 注册表 — 新增 action type 只需注册 Handler，不修改 Orchestrator"""

    def __init__(self):
        self._handlers: dict[str, ActionHandler] = {}

    def register(self, handler: ActionHandler):
        self._handlers[handler.action_type] = handler
        logger.info("[ActionRegistry] 注册 handler: %s", handler.action_type)

    async def handle(self, action, context, orchestrator) -> None:
        handler = self._handlers.get(action.action_type)
        if not handler:
            raise ControlActionError(f"未知 action type: {action.action_type}")
        await handler.execute(action, context, orchestrator)

    def create_default(self) -> ActionRegistry:
        """创建并注册默认的 action handlers"""
        self.register(RetryHandler())
        self.register(TerminateHandler())
        return self

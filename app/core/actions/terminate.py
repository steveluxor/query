import logging

from app.core.actions.base import ActionHandler
from app.models.control import ControlAction

logger = logging.getLogger(__name__)


class TerminateHandler(ActionHandler):
    """终止处理器：标记所有 pending 任务为 skipped，停止 DAG 执行"""

    @property
    def action_type(self) -> str:
        return "terminate"

    async def execute(self, action: ControlAction, context, orchestrator) -> None:
        plan = context.plan
        if not plan:
            return

        logger.info("[TerminateHandler] 终止执行")
        for t in plan.tasks:
            if t.status == "pending" or t.status == "retrying":
                t.status = "skipped"

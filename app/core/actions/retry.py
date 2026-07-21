import logging

from app.core.actions.base import ActionHandler
from app.models.control import ControlAction

logger = logging.getLogger(__name__)


class RetryHandler(ActionHandler):
    """重试处理器：无效化目标 task 及其下游，触发子图重新执行"""

    @property
    def action_type(self) -> str:
        return "retry"

    async def execute(self, action: ControlAction, context, orchestrator) -> None:
        plan = context.plan
        if not plan:
            logger.warning("[RetryHandler] 无 plan，忽略 retry action")
            return

        target = action.target_task_id
        if not target:
            logger.warning("[RetryHandler] retry action 缺少 target_task_id")
            return

        # target 是 agent 名称（如 "retrieval"、"analysis"），不是 task ID
        # 在 plan 中搜索所有 agent 名匹配的 task
        if target == "all":
            task_ids = {t.id for t in plan.tasks}
        else:
            task_ids = {t.id for t in plan.tasks if t.agent == target}

        if not task_ids:
            logger.warning("[RetryHandler] 未找到 agent=%s 的 task，忽略", target)
            return

        logger.info("[RetryHandler] 按 agent=%s 匹配到 %s", target, sorted(task_ids))
        affected = plan.invalidate_subgraph(task_ids)
        logger.info("[RetryHandler] 受影响 %d 个 task: %s", len(affected), sorted(affected))

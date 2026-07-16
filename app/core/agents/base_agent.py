import time
import logging
from abc import ABC, abstractmethod

from app.core.agent_context import AgentContext, AgentStep
from app.models.data_types import AgentTrace
from app.models.capability import AgentCapability

logger = logging.getLogger(__name__)


class BaseAgent(ABC):
    """Agent 基类：统一日志、计时、错误处理"""

    name: str = "base"
    capability: AgentCapability | None = None

    @abstractmethod
    async def run(self, context: AgentContext, **kwargs) -> AgentContext:
        ...

    async def execute(self, context: AgentContext, task_id: str = "", **kwargs) -> AgentContext:
        """包装 run()，添加计时、日志和执行轨迹"""
        start = time.time()
        logger.info("[%s] 开始执行 (task=%s)", self.name, task_id or "-")
        try:
            context = await self.run(context, **kwargs)
            duration = int((time.time() - start) * 1000)
            context.steps.append(AgentStep(
                name=self.name,
                duration_ms=duration,
                summary=f"完成，耗时 {duration}ms",
            ))
            context.add_trace(AgentTrace(
                task_id=task_id,
                agent=self.name,
                start_time=str(int(start * 1000)),
                end_time=str(int(time.time() * 1000)),
            ))
            logger.info("[%s] 执行完成，耗时 %dms", self.name, duration)
        except Exception as e:
            duration = int((time.time() - start) * 1000)
            context.steps.append(AgentStep(
                name=self.name,
                duration_ms=duration,
                summary=f"失败: {e}",
            ))
            logger.error("[%s] 执行失败: %s", self.name, e)
            raise
        return context

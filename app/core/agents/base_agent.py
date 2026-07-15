import time
import logging
from abc import ABC, abstractmethod

from app.core.agent_context import AgentContext, AgentStep

logger = logging.getLogger(__name__)


class BaseAgent(ABC):
    """Agent 基类：统一日志、计时、错误处理"""

    name: str = "base"

    @abstractmethod
    async def run(self, context: AgentContext) -> AgentContext:
        ...

    async def execute(self, context: AgentContext) -> AgentContext:
        """包装 run()，添加计时和日志"""
        start = time.time()
        logger.info("[%s] 开始执行", self.name)
        try:
            context = await self.run(context)
            duration = int((time.time() - start) * 1000)
            context.steps.append(AgentStep(
                name=self.name,
                duration_ms=duration,
                summary=f"完成，耗时 {duration}ms",
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

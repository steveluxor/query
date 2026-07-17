import time
import logging
from abc import ABC, abstractmethod
from typing import Any

from app.core.agent_context import AgentContext, AgentStep
from app.models.data_types import AgentTrace
from app.models.capability import AgentCapability
from app.models.control import ControlAction

logger = logging.getLogger(__name__)


class BaseAgent(ABC):
    """Agent 基类：统一日志、计时、错误处理"""

    name: str = "base"
    capability: AgentCapability | None = None

    @abstractmethod
    async def run(self, context: AgentContext, **kwargs) -> AgentContext:
        ...

    async def execute(self, context: AgentContext, task_id: str = "", **kwargs) -> Any:
        """包装 run()，添加计时、日志和执行轨迹"""
        start = time.time()
        logger.info("[%s] 开始执行 (task=%s)", self.name, task_id or "-")
        try:
            result = await self.run(context, **kwargs)
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
            return result
        except Exception as e:
            duration = int((time.time() - start) * 1000)
            context.steps.append(AgentStep(
                name=self.name,
                duration_ms=duration,
                summary=f"失败: {e}",
            ))
            logger.error("[%s] 执行失败: %s", self.name, e)
            raise


class ControllerAgent(BaseAgent):
    """Controller Agent 基类 — 返回 ControlAction 控制 Runtime 行为

    子类应实现：
      - run(): 执行判断逻辑，写入 context
      - parse_actions(): 从 context 提取 ControlAction 列表
    """

    async def execute(self, context: AgentContext, task_id: str = "", **kwargs) -> list[ControlAction]:
        """执行 Controller，返回 ControlAction 列表"""
        start = time.time()
        logger.info("[%s] Controller 开始执行 (task=%s)", self.name, task_id or "-")
        try:
            await self.run(context, **kwargs)
            actions = self.parse_actions(context)
            duration = int((time.time() - start) * 1000)
            context.steps.append(AgentStep(
                name=self.name,
                duration_ms=duration,
                summary=f"完成，{len(actions)} 个 control action，耗时 {duration}ms",
            ))
            context.add_trace(AgentTrace(
                task_id=task_id,
                agent=self.name,
                start_time=str(int(start * 1000)),
                end_time=str(int(time.time() * 1000)),
            ))
            logger.info("[%s] Controller 完成，%d actions，耗时 %dms", self.name, len(actions), duration)
            return actions
        except Exception as e:
            duration = int((time.time() - start) * 1000)
            logger.error("[%s] Controller 执行失败: %s", self.name, e)
            raise

    def parse_actions(self, context: AgentContext) -> list[ControlAction]:
        """子类实现：从 context 提取 ControlAction 列表"""
        return []

import logging

from app.core.agents.base_agent import BaseAgent
from app.core.agent_context import AgentContext
from app.models.capability import AgentCapability

logger = logging.getLogger(__name__)


class ChatAgent(BaseAgent):
    """闲聊 Agent：问候、打招呼等无需检索的简单对话"""

    name = "Chat"
    capability = AgentCapability(
        name="chat",
        description="问候、闲聊等无需检索知识的简单对话",
        outputs={
            "answer": str,
        },
    )

    async def run(self, context: AgentContext, **kwargs) -> AgentContext:
        context.set_output("answer", "你好！我是智能问答助手，请问有什么可以帮助你的？", producer="chat")
        return context

import json
import logging

from app.core.agents.base_agent import BaseAgent
from app.core.agent_context import AgentContext
from app.models.capability import AgentCapability

logger = logging.getLogger(__name__)


class ChatAgent(BaseAgent):
    """闲聊 Agent：问候、打招呼、偏好设置等无需检索的简单对话"""

    name = "Chat"
    capability = AgentCapability(
        name="chat",
        description="问候、闲聊、偏好设置等无需检索知识的简单对话",
        outputs={
            "answer": str,
        },
    )

    async def run(self, context: AgentContext, **kwargs) -> AgentContext:
        from app.core.llm_factory import create_llm
        llm = create_llm(temperature=0.1, max_tokens=512)

        prefs_text = ""
        if context.preferences:
            prefs_text = f"\n用户偏好：{json.dumps(context.preferences, ensure_ascii=False)}"

        prompt = (
            "你是一个智能问答助手。请用友好简洁的方式回答用户的问题。"
            "如果是问候，简短打招呼即可。"
            "如果是设置偏好或改变回答风格的指令，确认并友好回应。"
            f"{prefs_text}\n\n"
            f"用户说：{context.question}"
        )
        try:
            resp = llm.invoke(prompt)
            context.set_output("answer", resp.content.strip(), producer="chat")
        except Exception as e:
            logger.warning("[ChatAgent] LLM 调用失败，使用默认回答: %s", e)
            context.set_output("answer", "你好！我是智能问答助手，请问有什么可以帮助你的？", producer="chat")
        return context

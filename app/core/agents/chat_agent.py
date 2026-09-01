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

    @staticmethod
    def _format_history(history: list[dict] | None) -> str:
        if not history:
            return ""
        lines = []
        for item in history[-5:]:
            if hasattr(item, "question"):
                question = getattr(item, "question", "")
                answer = getattr(item, "answer", "")
            else:
                question = item.get("question", "")
                answer = item.get("answer", "")
            lines.append(f"用户: {str(question)[:160]}\n助手: {str(answer)[:400]}")
        return "\n---\n".join(lines)

    async def run(self, context: AgentContext, **kwargs) -> AgentContext:
        from app.core.infra.llm_factory import create_llm
        llm = create_llm(temperature=0.1, max_tokens=512)

        prefs_text = ""
        if context.preferences:
            prefs_text = f"\n用户偏好：{json.dumps(context.preferences, ensure_ascii=False)}"

        history_text = self._format_history(context.history)
        memory_text = (context.memory_context or "")[:1500]
        context_blocks = []
        if history_text:
            context_blocks.append(f"最近对话历史：\n{history_text}")
        if memory_text:
            context_blocks.append(f"长期记忆：\n{memory_text}")
        context_text = "\n\n".join(context_blocks)

        prompt = (
            "你是一个智能问答助手。请用友好简洁的方式回答用户的问题。"
            "如果是问候，简短打招呼即可。"
            "如果是设置偏好或改变回答风格的指令，确认并友好回应。"
            "对追问，先利用给出的历史和长期记忆消解指代；如果其中不足以确定答案，"
            "明确说明需要更多上下文或重新查询，不要编造。"
            f"{prefs_text}\n\n"
            f"{context_text}\n\n"
            f"用户说：{context.question}"
        )
        try:
            resp = llm.invoke(prompt)
            context.set_output("answer", resp.content.strip(), producer="chat")
        except Exception as e:
            logger.warning("[ChatAgent] LLM 调用失败，使用默认回答: %s", e)
            context.set_output("answer", "你好！我是智能问答助手，请问有什么可以帮助你的？", producer="chat")
        return context

import json
import logging

from langchain_openai import ChatOpenAI

from app.config import settings
from app.core.agents.base_agent import BaseAgent
from app.core.agent_context import AgentContext
from app.prompt_manager import PromptManager

logger = logging.getLogger(__name__)


class CriticAgent(BaseAgent):
    """Critic Agent：独立审核答案质量，输出 verdict 和 feedback"""

    name = "Critic"

    def __init__(self):
        self.llm = ChatOpenAI(
            api_key=settings.llm_api_key,
            base_url=settings.llm_base_url,
            model=settings.llm_model_name,
            temperature=0,
            timeout=30,
        )

    async def run(self, context: AgentContext) -> AgentContext:
        sources_text = "\n".join(
            f"- [{s.get('file_name', '')}] {s.get('content', '')[:200]}"
            for s in context.sources[:5]
        )
        prompt = PromptManager.get("critic", "evaluate").format(
            question=context.question,
            sources=sources_text,
            answer=context.answer,
        )
        try:
            result = self.llm.invoke([("human", prompt)])
            reflection = json.loads(result.content)
        except Exception:
            reflection = {"verdict": "ok"}

        if reflection.get("verdict") == "ok":
            logger.info("[Critic] 答案通过审核")
        else:
            feedback = reflection.get("feedback", "")
            context.critique = feedback
            context.reflection_count += 1
            logger.info("[Critic] 答案需要修改: %s", feedback[:100])

        return context

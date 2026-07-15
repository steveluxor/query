import json
import logging

from langchain_openai import ChatOpenAI
from langchain_core.messages import SystemMessage, HumanMessage

from app.config import settings
from app.core.agents.base_agent import BaseAgent
from app.core.agent_context import AgentContext

logger = logging.getLogger(__name__)

CLASSIFY_SYSTEM = """你是一个问题分类器。根据用户问题判断是否需要以下三种处理：

1. needs_plan: 问题需要多步骤执行（跨文档对比、先搜索再计算、多条件筛选等）。简单查询（单次搜索即可回答）为 false。
2. needs_analysis: 问题需要数值计算（求和、排名、统计、比较大小、排序、加减乘除等）。为 true 时 Analysis Agent 将使用 calculate_sum/calculate_rank 工具。
3. needs_review: 问题较复杂，需要审核答案质量（分析、对比、总结、建议、评估、趋势、原因探究等）

简单问答（如问候、闲聊、简单的事实查询）三个都是 false。

只返回 JSON，不要其他内容。示例：
{"needs_plan": false, "needs_analysis": false, "needs_review": false}"""


class CoordinatorAgent(BaseAgent):
    """任务路由 Agent：LLM 分类判断"""

    name = "Coordinator"

    def __init__(self):
        self.needs_review = False
        self.needs_analysis = False
        self.needs_plan = False
        self.llm = ChatOpenAI(
            api_key=settings.llm_api_key,
            base_url=settings.llm_base_url,
            model=settings.llm_model_name,
            temperature=0,
            max_tokens=50,
            timeout=30,
        )

    async def run(self, context: AgentContext) -> AgentContext:
        result = self._classify(context.question)
        self.needs_plan = result.get("needs_plan", False)
        self.needs_analysis = result.get("needs_analysis", False)
        self.needs_review = result.get("needs_review", False)
        logger.info("分类结果: needs_plan=%s, needs_analysis=%s, needs_review=%s", self.needs_plan, self.needs_analysis, self.needs_review)
        return context

    def _classify(self, question: str) -> dict:
        try:
            resp = self.llm.invoke([
                SystemMessage(content=CLASSIFY_SYSTEM),
                HumanMessage(content=question),
            ])
            text = resp.content.strip()
            # 提取 JSON（兼容 markdown 代码块包裹）
            if "```" in text:
                text = text.split("```")[1]
                if text.startswith("json"):
                    text = text[4:]
                text = text.strip()
            return json.loads(text)
        except Exception as e:
            logger.warning("LLM 分类失败，默认不触发分析/审核: %s", e)
            return {"needs_analysis": False, "needs_review": False}

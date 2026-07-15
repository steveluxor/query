import logging

from langchain.agents import create_agent

from app.core.agents.base_agent import BaseAgent
from app.core.agent_context import AgentContext
from app.mcp_client import MCPClient
from app.mcp_tools import create_mcp_tools

logger = logging.getLogger(__name__)


class AnalysisAgent(BaseAgent):
    """数据分析 Agent：通过 MCP Client 调用计算工具"""

    name = "Analysis"

    def __init__(self, rag_engine, mcp_client: MCPClient):
        self.engine = rag_engine
        self.mcp_client = mcp_client

    async def run(self, context: AgentContext) -> AgentContext:
        tools = create_mcp_tools(self.mcp_client)

        system_prompt = (
            "你是一个数据分析专家。基于已检索到的数据，执行精确计算。\n"
            "可用工具：calculate_sum（求和）、calculate_rank（排名）、read_all_rows（读取全部数据）\n"
            "规则：\n"
            "- 计算结果以工具返回值为准，不要自行编造\n"
            "- 计算工具最多调用8次\n"
            "- 如果数据不足，直接说明"
        )

        if context.memory_context:
            system_prompt += f"\n\n<长期记忆>\n{context.memory_context}\n</长期记忆>"

        agent = create_agent(
            model=self.engine.llm,
            tools=tools,
            system_prompt=system_prompt,
        )

        try:
            result = await agent.ainvoke(
                {"messages": [("human", context.question)]},
                config={"recursion_limit": 15},
            )
            context.answer = result["messages"][-1].content
        except Exception as e:
            logger.error("[Analysis] Agent 执行失败: %s", e)
            context.answer = "抱歉，计算时出现错误，请稍后重试。"

        return context

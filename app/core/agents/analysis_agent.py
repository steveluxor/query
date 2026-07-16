import json
import logging

from langchain.agents import create_agent

from app.core.agents.base_agent import BaseAgent
from app.core.agent_context import AgentContext
from app.core.rag_engine import RAGEngine
from app.core.mcp.client import MCPClient
from app.core.mcp.tools import create_mcp_tools
from app.models.data_types import AnalysisResult, Calculation
from app.models.capability import AgentCapability

logger = logging.getLogger(__name__)

ANALYSIS_SYSTEM_PROMPT = """你是一个数据分析专家。基于已检索到的数据，执行精确计算。

工具：
- calculate_sum: 对指定列求和
- calculate_rank: 对指定列排序并返回第N名

规则：
- 必须通过工具获取计算结果，不要自行编造数值
- 计算工具最多调用8次
- 如果数据不足，直接说明

你必须以 JSON 格式输出，不要输出任何自然语言。
如果无法完成分析，返回 {"calculations": [], "findings": [], "conclusions": []}。

输出格式：
{
  "calculations": [
    {
      "operation": "sum",
      "field": "price",
      "arguments": {"row_filter": "", "content_filter": ""},
      "result": 5000,
      "source": "sales.xlsx"
    }
  ],
  "findings": ["发现1", "发现2"],
  "conclusions": ["结论1"]
}"""


class AnalysisAgent(BaseAgent):
    """数据分析 Agent：通过 MCP 工具执行计算并输出结构化结果"""

    name = "Analysis"
    capability = AgentCapability(
        name="analysis",
        description="数据分析，求和、排名",
        tools=["calculate_sum", "calculate_rank"],
        writes_to=["analysis"],
        requires=["evidence"],
    )

    def __init__(self, rag_engine: RAGEngine, mcp_client: MCPClient):
        self.engine = rag_engine
        self.mcp_client = mcp_client

    async def run(self, context: AgentContext) -> AgentContext:
        tools = create_mcp_tools(self.mcp_client, include=["calculate_sum", "calculate_rank"])

        system_prompt = ANALYSIS_SYSTEM_PROMPT

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

            raw_messages = result["messages"]
            analysis = self._parse_analysis(raw_messages)
            context.set_analysis(analysis)

            logger.info("[Analysis] 提取 %d 个计算, %d 个发现",
                        len(analysis.calculations), len(analysis.findings))

        except Exception as e:
            logger.error("[Analysis] Agent 执行失败: %s", e)
            context.set_analysis(AnalysisResult())

        return context

    def _parse_analysis(self, messages) -> AnalysisResult:
        """从 LLM 最终输出中解析 AnalysisResult JSON"""
        for msg in reversed(messages):
            if hasattr(msg, "type") and msg.type == "ai" and not getattr(msg, "tool_calls", None):
                content = msg.content
                return self._extract_analysis_from_text(content)
        return AnalysisResult()

    def _extract_analysis_from_text(self, text: str) -> AnalysisResult:
        """从文本中提取 AnalysisResult"""
        logger.warning("[Analysis] LLM 原始输出 (前500字符): %s", text[:500])
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            if "```" in text:
                parts = text.split("```")
                for part in parts:
                    part = part.strip()
                    if part.startswith("json"):
                        part = part[4:]
                    try:
                        data = json.loads(part.strip())
                        break
                    except json.JSONDecodeError:
                        continue
                else:
                    return AnalysisResult()
            else:
                start = text.find("{")
                end = text.rfind("}")
                if start != -1 and end > start:
                    try:
                        data = json.loads(text[start:end + 1])
                    except json.JSONDecodeError:
                        return AnalysisResult()
                else:
                    return AnalysisResult()

        calculations = []
        for item in data.get("calculations", []):
            if isinstance(item, dict):
                calculations.append(Calculation(
                    operation=item.get("operation", ""),
                    field=item.get("field", ""),
                    arguments=item.get("arguments", {}),
                    result=item.get("result"),
                    source=item.get("source", ""),
                ))

        return AnalysisResult(
            calculations=calculations,
            findings=data.get("findings", []),
            conclusions=data.get("conclusions", []),
        )

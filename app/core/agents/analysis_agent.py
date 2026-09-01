import logging

from app.core.agents.base_agent import BaseAgent
from app.core.agent_context import AgentContext, _task_objective_var
from app.core.infra.llm_factory import create_llm
from app.core.mcp.client import MCPClient
from app.core.mcp.tool_loop import run_tool_loop
from app.core.prompts.prompt_manager import PromptManager
from app.models.data_types import AnalysisResult, Calculation
from app.models.capability import AgentCapability

logger = logging.getLogger(__name__)

# Analysis 可用的 MCP 工具（不暴露 search/list/管理类工具，保持 DAG 隔离）
_ANALYSIS_TOOLS = ["read_all_rows", "calculate_sum", "calculate_rank"]


class AnalysisAgent(BaseAgent):
    """数据分析 Agent：LLM 自主调用 MCP 工具（read_all_rows/calculate_sum/calculate_rank）执行计算并输出结构化结果"""

    name = "Analysis"
    capability = AgentCapability(
        name="analysis",
        description="数据分析，求和、排名",
        outputs={
            "analysis": AnalysisResult,
        },
        tools=["calculate_sum", "calculate_rank"],
        tool_descriptions={
            "calculate_sum": "数值求和",
            "calculate_rank": "数值排名",
        },
        merge_policy={
            "analysis": "replace",
        },
    )

    def __init__(self, llm=None):
        self.llm = llm

    async def run(self, context: AgentContext, mcp_client: MCPClient = None, mcp_session_id: str = "", **kwargs) -> AgentContext:
        system_prompt = PromptManager.get("analysis", "system")

        llm = self.llm or create_llm()
        user_prompt = _task_objective_var.get() or context.question

        try:
            messages = await run_tool_loop(
                llm=llm,
                mcp_client=mcp_client,
                session_id=mcp_session_id,
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                tool_names=_ANALYSIS_TOOLS,
            )

            analysis = self._parse_analysis(messages)
            context.set_output("analysis", analysis, producer="analysis")

            logger.info("[Analysis] 提取 %d 个计算, %d 个发现",
                        len(analysis.calculations), len(analysis.findings))

        except Exception as e:
            logger.error("[Analysis] Agent 执行失败: %s", e)
            # 失败即 FAILED，交由 Orchestrator 跳过下游，避免空 AnalysisResult 产生错误回答
            raise

        return context

    def _parse_analysis(self, messages) -> AnalysisResult:
        """从消息列表中解析 AnalysisResult JSON（取最后一条无 tool_calls 的 AI 消息）"""
        for msg in reversed(messages):
            if hasattr(msg, "type") and msg.type == "ai" and not getattr(msg, "tool_calls", None):
                content = msg.content
                return self._extract_analysis_from_text(content)
        return AnalysisResult()

    def _extract_analysis_from_text(self, text: str) -> AnalysisResult:
        """从文本中提取 AnalysisResult"""
        logger.warning("[Analysis] LLM 原始输出 (前500字符): %s", text[:500])
        from app.core.utils import extract_json
        data = extract_json(text)
        if data is None or not isinstance(data, dict):
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

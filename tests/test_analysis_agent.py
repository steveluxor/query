"""AnalysisAgent 单元测试"""

from unittest.mock import AsyncMock, MagicMock

import pytest
from langchain_core.messages import AIMessage

from app.core.agent_context import AgentContext
from app.core.agents.analysis_agent import AnalysisAgent
from app.models.data_types import AnalysisResult


@pytest.fixture
def mock_llm():
    return MagicMock()


@pytest.fixture
def agent(mock_llm):
    return AnalysisAgent(mock_llm)


@pytest.fixture
def context():
    return AgentContext(question="计算2024年总销售额")


VALID_ANALYSIS_JSON = '''{
  "calculations": [
    {"operation": "sum", "field": "销售额", "result": 5000, "source": "sales.xlsx"}
  ],
  "findings": ["2024年总销售额5000"],
  "conclusions": ["目标完成"]
}'''


def _mock_mcp(list_tools=None):
    """返回 list_tools 为 AsyncMock 的假 MCP Client"""
    mcp = MagicMock()
    mcp.list_tools = AsyncMock(return_value=list_tools or [])
    return mcp


def _llm_returning(final_content: str):
    """返回 ainvoke 结果为指定内容 AIMessage 的假 LLM（无工具调用）"""
    llm = MagicMock()
    llm.ainvoke = AsyncMock(return_value=AIMessage(content=final_content))
    return llm


def test_tool_loop_import_exists():
    """验证原生工具循环模块可导入"""
    from app.core.mcp.tool_loop import build_tool_schemas, run_tool_loop
    assert callable(build_tool_schemas)
    assert callable(run_tool_loop)


@pytest.mark.anyio
async def test_empty_result_on_parse_failure(agent, context):
    """LLM 返回无效内容时返回空的 AnalysisResult"""
    agent.llm = _llm_returning("不是JSON")
    await agent.run(context, mcp_client=_mock_mcp(), mcp_session_id="s1")

    analysis = context.get_output("analysis")
    assert isinstance(analysis, AnalysisResult)
    assert len(analysis.calculations) == 0


@pytest.mark.anyio
async def test_analysis_parses_content(agent, context):
    """正常解析 AnalysisResult"""
    agent.llm = _llm_returning(VALID_ANALYSIS_JSON)
    await agent.run(context, mcp_client=_mock_mcp(), mcp_session_id="s1")

    analysis = context.get_output("analysis")
    assert isinstance(analysis, AnalysisResult)
    assert len(analysis.calculations) == 1
    assert analysis.calculations[0].operation == "sum"
    assert analysis.calculations[0].field == "销售额"
    assert analysis.calculations[0].result == 5000
    assert len(analysis.findings) == 1
    assert len(analysis.conclusions) == 1


def test_extract_analysis_from_text_valid(agent):
    """直接测试 _extract_analysis_from_text"""
    result = agent._extract_analysis_from_text(VALID_ANALYSIS_JSON)
    assert isinstance(result, AnalysisResult)
    assert len(result.calculations) == 1


def test_extract_analysis_from_text_invalid(agent):
    """无效文本返回空"""
    result = agent._extract_analysis_from_text("plain text")
    assert isinstance(result, AnalysisResult)
    assert len(result.calculations) == 0


def test_extract_analysis_from_text_none(agent):
    """None 输入"""
    result = agent._extract_analysis_from_text("")
    assert isinstance(result, AnalysisResult)

    result = agent._extract_analysis_from_text("null")
    assert isinstance(result, AnalysisResult)

"""AnalysisAgent 单元测试"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.core.agent_context import AgentContext
from app.core.agents.analysis_agent import AnalysisAgent
from app.models.data_types import AnalysisResult


@pytest.fixture
def mock_rag_engine():
    engine = MagicMock()
    engine.llm = MagicMock()
    return engine


@pytest.fixture
def agent(mock_rag_engine):
    return AnalysisAgent(mock_rag_engine)


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


@pytest.mark.anyio
async def test_create_agent_import_exists():
    """验证 create_agent 可导入"""
    from langchain.agents import create_agent
    assert callable(create_agent)


@pytest.mark.anyio
async def test_empty_result_on_parse_failure(agent, context):
    """LLM 返回无效内容时返回空的 AnalysisResult"""
    mock_llm = MagicMock()
    mock_llm.ainvoke = AsyncMock(return_value=MagicMock(content="不是JSON"))

    # Mock create_agent 返回的对象
    mock_agent = MagicMock()
    mock_agent.ainvoke = AsyncMock(return_value={
        "messages": [MagicMock(type="ai", tool_calls=None, content="不是JSON")],
    })

    with patch("app.core.agents.analysis_agent.create_agent", return_value=mock_agent):
        await agent.run(context, mcp_client=MagicMock(), mcp_session_id="s1")

    analysis = context.get_output("analysis")
    assert isinstance(analysis, AnalysisResult)
    assert len(analysis.calculations) == 0


@pytest.mark.anyio
async def test_analysis_parses_content(agent, context):
    """正常解析 AnalysisResult"""
    mock_agent = MagicMock()
    mock_agent.ainvoke = AsyncMock(return_value={
        "messages": [MagicMock(type="ai", tool_calls=None, content=VALID_ANALYSIS_JSON)],
    })

    with patch("app.core.agents.analysis_agent.create_agent", return_value=mock_agent):
        await agent.run(context, mcp_client=MagicMock(), mcp_session_id="s1")

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

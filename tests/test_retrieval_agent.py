"""RetrievalAgent 单元测试"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.core.agent_context import AgentContext
from app.core.agents.retrieval_agent import RetrievalAgent
from app.models.data_types import DocumentBundle


@pytest.fixture
def agent():
    return RetrievalAgent()


@pytest.fixture
def context():
    return AgentContext(question="total sales for 2024")


@pytest.fixture
def mock_mcp():
    client = AsyncMock()
    client.call_tool = AsyncMock()
    return client


@pytest.mark.anyio
async def test_generate_query_returns_json(agent):
    """LLM returns JSON, correctly parses query and type"""
    mock_llm = MagicMock()
    mock_llm.ainvoke = AsyncMock(return_value=MagicMock(
        content='{"type": "aggregation", "query": "sales 2024"}'
    ))
    with patch("app.core.agents.retrieval_agent.create_llm", return_value=mock_llm):
        query, query_type = await agent._generate_query("total sales for 2024")
    assert query == "sales 2024"
    assert query_type == "aggregation"


@pytest.mark.anyio
async def test_generate_query_non_json_fallback(agent):
    """LLM returns non-JSON text, falls back to comparison"""
    mock_llm = MagicMock()
    mock_llm.ainvoke = AsyncMock(return_value=MagicMock(content="sales 2024"))
    with patch("app.core.agents.retrieval_agent.create_llm", return_value=mock_llm):
        query, query_type = await agent._generate_query("total sales for 2024")
    assert query == "sales 2024"
    assert query_type == "comparison"


@pytest.mark.anyio
async def test_generate_query_fallback_to_original(agent):
    """LLM fails, falls back to original question + comparison"""
    mock_llm = MagicMock()
    mock_llm.ainvoke = AsyncMock(side_effect=Exception("LLM error"))
    with patch("app.core.agents.retrieval_agent.create_llm", return_value=mock_llm):
        query, query_type = await agent._generate_query("total sales for 2024")
    assert query == "total sales for 2024"
    assert query_type == "comparison"


@pytest.mark.anyio
async def test_run_no_results(agent, context, mock_mcp):
    """Search returns empty results"""
    mock_mcp.call_tool.return_value = "没有找到相关内容"
    mock_llm = MagicMock()
    mock_llm.ainvoke = AsyncMock(return_value=MagicMock(content='{"type": "aggregation", "query": "sales"}'))
    with patch("app.core.agents.retrieval_agent.create_llm", return_value=mock_llm):
        result = await agent.run(context, mcp_client=mock_mcp, mcp_session_id="s1")

    bundle = context.get_output("document_bundle")
    assert isinstance(bundle, DocumentBundle)
    assert len(bundle.chunks) == 0


@pytest.mark.anyio
async def test_run_with_search_and_full_data(agent, context, mock_mcp):
    """Full search + read_all_rows flow"""
    mock_mcp.call_tool = AsyncMock(side_effect=[
        '检索到以下相关内容\n[doc1.pdf]\n内容1',
        '以下是完整数据\n[doc1.pdf]\n完整内容',
    ])
    mock_llm = MagicMock()
    mock_llm.ainvoke = AsyncMock(return_value=MagicMock(content='{"type": "comparison", "query": "sales"}'))
    with patch("app.core.agents.retrieval_agent.create_llm", return_value=mock_llm):
        result = await agent.run(context, mcp_client=mock_mcp, mcp_session_id="s1")

    bundle = context.get_output("document_bundle")
    assert isinstance(bundle, DocumentBundle)
    assert len(bundle.chunks) > 0
    assert bundle.chunks[0].source == "doc1.pdf"

    report = context.get_output("retrieval_report")
    assert report is not None
    assert report.read_all_rows_called is True


@pytest.mark.anyio
async def test_run_search_failure(agent, context, mock_mcp):
    """search_documents fails, graceful degradation"""
    mock_llm = MagicMock()
    mock_llm.ainvoke = AsyncMock(return_value=MagicMock(content='{"type": "aggregation", "query": "sales"}'))
    mock_mcp.call_tool = AsyncMock(side_effect=Exception("MCP error"))
    with patch("app.core.agents.retrieval_agent.create_llm", return_value=mock_llm):
        result = await agent.run(context, mcp_client=mock_mcp, mcp_session_id="s1")

    bundle = context.get_output("document_bundle")
    assert isinstance(bundle, DocumentBundle)
    assert len(bundle.chunks) == 0


@pytest.mark.anyio
async def test_run_aggregation_uses_strict_strategy(agent, context, mock_mcp):
    """aggregation type uses strict strategy"""
    mock_mcp.call_tool = AsyncMock(side_effect=[
        '检索到以下相关内容\n[doc1.pdf]\n内容1',
        '以下是完整数据\n[doc1.pdf]\n完整内容',
    ])
    mock_llm = MagicMock()
    mock_llm.ainvoke = AsyncMock(return_value=MagicMock(content='{"type": "aggregation", "query": "sales"}'))
    with patch("app.core.agents.retrieval_agent.create_llm", return_value=mock_llm):
        await agent.run(context, mcp_client=mock_mcp, mcp_session_id="s1")

    call = mock_mcp.call_tool.call_args_list[0]
    assert call[0][0] == "search_documents"
    assert call[0][1]["strategy"] == "strict"


@pytest.mark.anyio
async def test_run_comparison_uses_standard_strategy(agent, context, mock_mcp):
    """comparison type uses standard strategy"""
    mock_mcp.call_tool = AsyncMock(side_effect=[
        '检索到以下相关内容\n[doc1.pdf]\n内容1',
        '以下是完整数据\n[doc1.pdf]\n完整内容',
    ])
    mock_llm = MagicMock()
    mock_llm.ainvoke = AsyncMock(return_value=MagicMock(content='{"type": "comparison", "query": "experiment"}'))
    with patch("app.core.agents.retrieval_agent.create_llm", return_value=mock_llm):
        await agent.run(context, mcp_client=mock_mcp, mcp_session_id="s1")

    call = mock_mcp.call_tool.call_args_list[0]
    assert call[0][0] == "search_documents"
    assert call[0][1]["strategy"] == "standard"


@pytest.mark.anyio
async def test_parse_document_bundle_empty(agent):
    """Empty text parses to empty DocumentBundle"""
    bundle = agent._parse_document_bundle("", "")
    assert len(bundle.chunks) == 0


@pytest.mark.anyio
async def test_parse_document_bundle_with_content(agent):
    """Parse text with [source] markers"""
    search_text = '检索到以下相关内容\n[doc1.pdf]\n第一段\n[doc2.xlsx]\n第二段'
    bundle = agent._parse_document_bundle(search_text, "")
    assert len(bundle.chunks) == 2
    assert bundle.chunks[0].source == "doc1.pdf"
    assert bundle.chunks[1].source == "doc2.xlsx"

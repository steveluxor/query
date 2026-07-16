"""共享测试 fixtures"""
import sys
import os
from unittest.mock import MagicMock

import pytest

# 确保 app 包可导入
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from app.core.agent_context import AgentContext
from app.models.data_types import Evidence, AnalysisResult


@pytest.fixture
def make_context():
    """AgentContext 工厂 fixture"""
    def _make(question: str = "测试问题", session_id: str = "test_session", **kwargs) -> AgentContext:
        return AgentContext(question=question, session_id=session_id, **kwargs)
    return _make


@pytest.fixture
def mock_llm():
    """Mock ChatOpenAI LLM"""
    mock = MagicMock()
    mock.predict = MagicMock(return_value='{"key": "value"}')
    mock.ainvoke = MagicMock(return_value=MagicMock(content='{"key": "value"}'))
    return mock


@pytest.fixture
def mock_vector_store():
    """Mock VectorStore"""
    mock = MagicMock()
    mock.similarity_search = MagicMock(return_value=[])
    mock.get_all_chunks = MagicMock(return_value=[])
    mock.get_document_names = MagicMock(return_value={})
    return mock


@pytest.fixture
def sample_evidence():
    """示例 Evidence 列表"""
    return [
        Evidence(
            statement="2024年销售额为5000万元",
            source="sales_2024.xlsx",
            evidence_type="fact",
            metadata={},
        ),
        Evidence(
            statement="同比增长15%",
            source="report.pdf",
            evidence_type="inference",
            metadata={},
        ),
    ]

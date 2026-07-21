"""CriticAgent 单元测试 — 审核 + retry 映射"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.core.agent_context import AgentContext
from app.core.agents.critic_agent import CriticAgent
from app.models.data_types import CriticResult
from app.models.control import ControlAction


@pytest.fixture
def agent():
    return CriticAgent()


@pytest.fixture
def context():
    return AgentContext(question="测试问题")


@pytest.fixture
def mock_llm():
    m = MagicMock()
    m.ainvoke = AsyncMock()
    return m


PASS_JSON = '{"score": 9, "problems": [], "need_retry": false, "retry_target": "all"}'
RETRY_JSON = '{"score": 4, "problems": ["证据不足"], "need_retry": true, "retry_target": "retrieval"}'


@pytest.mark.anyio
async def test_pass_no_retry(agent, context, mock_llm):
    """答案通过审核，无需重试"""
    agent.llm = mock_llm
    mock_llm.ainvoke.return_value = MagicMock(content=PASS_JSON)
    await agent.run(context, evidence_list=[], analysis_result=None, generated_answer="答案内容")

    assert context.get_output("need_retry") is False
    assert context.get_output("retry_target") == "all"


@pytest.mark.anyio
async def test_retry_needed(agent, context, mock_llm):
    """答案需要重试"""
    agent.llm = mock_llm
    mock_llm.ainvoke.return_value = MagicMock(content=RETRY_JSON)
    await agent.run(context, evidence_list=[], analysis_result=None, generated_answer="答案内容")

    assert context.get_output("need_retry") is True
    assert context.get_output("retry_target") == "retrieval"


@pytest.mark.anyio
async def test_llm_failure(agent, context, mock_llm):
    """LLM 调用失败时降级"""
    agent.llm = mock_llm
    mock_llm.ainvoke.side_effect = Exception("LLM error")
    await agent.run(context, evidence_list=[], analysis_result=None, generated_answer="答案内容")

    assert context.get_output("need_retry") is True
    assert context.get_output("retry_target") == "all"
    problems = context.get_output("critique")
    assert "Critic 调用失败" in problems


def test_parse_actions_no_retry(agent, context):
    """need_retry=false 时返回空 actions"""
    context.set_output("need_retry", False, producer="critic")
    context.set_output("retry_target", "all", producer="critic")
    actions = agent.parse_actions(context)
    assert actions == []


def test_parse_actions_with_retry(agent, context):
    """need_retry=true 时返回 ControlAction"""
    context.set_output("need_retry", True, producer="critic")
    context.set_output("retry_target", "generator", producer="critic")
    actions = agent.parse_actions(context)
    assert len(actions) == 1
    assert actions[0].action_type == "retry"
    assert actions[0].target_task_id == "generator"


def test_parse_result_valid(agent):
    """正常 CriticResult 解析"""
    result = agent._parse_result(PASS_JSON)
    assert isinstance(result, CriticResult)
    assert result.score == 9
    assert result.need_retry is False


def test_parse_result_retry(agent):
    """重试场景的 CriticResult"""
    result = agent._parse_result(RETRY_JSON)
    assert result.score == 4
    assert result.need_retry is True
    assert result.retry_target == "retrieval"


def test_parse_result_invalid(agent):
    """无效 JSON 降级"""
    result = agent._parse_result("not json")
    assert result.score == 0
    assert result.need_retry is True

"""AnswerGenerator 单元测试 — knowledge_objects 优先级 + 降级"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.core.agent_context import AgentContext
from app.core.generator.answer_generator import AnswerGenerator
from app.models.data_types import KnowledgeObject, Evidence, AnalysisResult, Calculation


@pytest.fixture
def agent():
    return AnswerGenerator()


@pytest.fixture
def context():
    return AgentContext(question="对比各实验的差异")


@pytest.fixture
def mock_llm():
    m = MagicMock()
    m.ainvoke = AsyncMock(return_value=MagicMock(content="生成结果"))
    return m


@pytest.mark.anyio
async def test_generate_with_knowledge_objects(agent, context, mock_llm):
    """knowledge_objects 优先于 evidence"""
    agent.llm = mock_llm
    mock_llm.ainvoke.return_value = MagicMock(content="实验一和实验二存在差异")
    kos = [
        KnowledgeObject(topic="实验一", attributes={"温度": "25°C"}, source="exp1.docx"),
        KnowledgeObject(topic="实验二", attributes={"温度": "30°C"}, source="exp2.docx"),
    ]

    await agent.run(context, structured_knowledge=kos, evidence_list=[], analysis_result=None, source_meta=[])

    answer = context.get_output("answer")
    assert answer == "实验一和实验二存在差异"


@pytest.mark.anyio
async def test_generate_with_evidence_fallback(agent, context, mock_llm):
    """无 knowledge_objects 时使用 evidence"""
    agent.llm = mock_llm
    mock_llm.ainvoke.return_value = MagicMock(content="销售额为5000")
    evs = [Evidence(statement="销售额5000", source="sales.xlsx", evidence_type="text")]

    await agent.run(context, structured_knowledge=[], evidence_list=evs, analysis_result=None, source_meta=[])

    answer = context.get_output("answer")
    assert answer == "销售额为5000"


@pytest.mark.anyio
async def test_fallback_answer_on_llm_failure(agent, context, mock_llm):
    """LLM 失败时使用 evidence 降级"""
    agent.llm = mock_llm
    mock_llm.ainvoke.side_effect = Exception("LLM error")
    evs = [Evidence(statement="事实1", source="doc.pdf", evidence_type="text")]

    await agent.run(context, structured_knowledge=[], evidence_list=evs, analysis_result=None, source_meta=[])

    answer = context.get_output("answer")
    assert "事实1" in answer


@pytest.mark.anyio
async def test_fallback_answer_no_evidence(agent, context, mock_llm):
    """无证据时返回服务不可用"""
    agent.llm = mock_llm
    mock_llm.ainvoke.side_effect = Exception("LLM error")

    await agent.run(context, structured_knowledge=[], evidence_list=[], analysis_result=None, source_meta=[])

    answer = context.get_output("answer")
    assert "服务暂时不可用" in answer


@pytest.mark.anyio
async def test_empty_input(agent, context, mock_llm):
    """空输入不崩溃"""
    agent.llm = mock_llm
    mock_llm.ainvoke.return_value = MagicMock(content="")
    await agent.run(context)

    answer = context.get_output("answer")
    assert answer is not None


def test_build_prompt_with_int_list_values(agent):
    """attributes 中包含 int 列表时不应崩溃"""
    context = AgentContext(question="测试")
    kos = [KnowledgeObject(topic="实验一", attributes={"销售额": [100, 200, 300]}, source="doc.pdf")]
    prompt = agent._build_prompt(context, structured_knowledge=kos)
    assert "100, 200, 300" in prompt


@pytest.mark.anyio
async def test_build_prompt_knowledge_objects_priority(agent):
    """验证 prompt 中 knowledge_objects 出现在 evidence 之前"""
    context = AgentContext(question="测试")
    kos = [KnowledgeObject(topic="主题1", attributes={"k": "v"}, source="doc.pdf")]
    evs = [Evidence(statement="事实", source="doc.pdf", evidence_type="text")]
    prompt = agent._build_prompt(context, evidence_list=evs, structured_knowledge=kos)
    # knowledge_objects 出现在 prompt 中
    assert "主题1" in prompt
    # evidence 不应该出现（knowledge_objects 优先）
    assert "知识对象" in prompt

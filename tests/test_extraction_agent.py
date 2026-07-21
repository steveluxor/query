"""ExtractionAgent 单元测试 — Map-Reduce + JSON 解析"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.core.agent_context import AgentContext
from app.core.agents.extraction_agent import ExtractionAgent
from app.models.data_types import DocumentBundle, DocumentChunk, KnowledgeObject, Evidence


@pytest.fixture
def agent():
    return ExtractionAgent()


@pytest.fixture
def context():
    return AgentContext(question="对比各实验的差异")


def make_bundle(items: list[tuple[str, str]]) -> DocumentBundle:
    """从 [(source, content), ...] 创建 DocumentBundle"""
    chunks = []
    for i, (src, content) in enumerate(items):
        chunks.append(DocumentChunk(source=src, content=content, chunk_index=i))
    return DocumentBundle(chunks=chunks)


VALID_JSON = '''{
  "knowledge_objects": [
    {
      "topic": "实验一",
      "attributes": {"温度": "25°C", "结果": "成功"},
      "source": "exp1.docx",
      "confidence": 0.95
    }
  ],
  "evidence": [
    {
      "statement": "实验一在25°C下成功",
      "source": "exp1.docx",
      "evidence_type": "text"
    }
  ]
}'''


@pytest.mark.anyio
async def test_empty_bundle(agent, context):
    """无文档时直接返回空"""
    bundle = DocumentBundle(chunks=[])
    await agent.run(context, knowledge_document=bundle)
    assert context.get_output("knowledge_objects") == []
    assert context.get_output("evidence") == []


@pytest.mark.anyio
async def test_single_document_extraction(agent, context):
    """单个文档的提取"""
    bundle = make_bundle([("exp1.docx", "实验一内容")])

    mock_llm = MagicMock()
    mock_llm.ainvoke = AsyncMock(return_value=MagicMock(content=VALID_JSON))
    with patch("app.core.agents.extraction_agent.create_llm", return_value=mock_llm):
        await agent.run(context, knowledge_document=bundle)

    kos = context.get_output("knowledge_objects")
    evs = context.get_output("evidence")
    assert len(kos) == 1
    assert kos[0].topic == "实验一"
    assert len(evs) == 1
    assert evs[0].statement == "实验一在25°C下成功"


@pytest.mark.anyio
async def test_multi_document_map_reduce(agent, context):
    """多文档 Map-Reduce 提取"""
    bundle = make_bundle([
        ("exp1.docx", "实验一内容"),
        ("exp2.docx", "实验二内容"),
    ])

    json2 = '''{
      "knowledge_objects": [{"topic": "实验二", "attributes": {"温度": "30°C"}, "source": "exp2.docx", "confidence": 0.9}],
      "evidence": [{"statement": "实验二在30°C下成功", "source": "exp2.docx", "evidence_type": "text"}]
    }'''

    mock_llm = MagicMock()
    mock_llm.ainvoke = AsyncMock(side_effect=[
        MagicMock(content=VALID_JSON),
        MagicMock(content=json2),
    ])
    with patch("app.core.agents.extraction_agent.create_llm", return_value=mock_llm):
        await agent.run(context, knowledge_document=bundle)

    kos = context.get_output("knowledge_objects")
    evs = context.get_output("evidence")
    assert len(kos) == 2
    assert kos[0].topic == "实验一"
    assert kos[1].topic == "实验二"


@pytest.mark.anyio
async def test_extraction_failure_one_doc(agent, context):
    """某个文档提取失败不影响其他文档"""
    bundle = make_bundle([
        ("exp1.docx", "内容"),
        ("exp2.docx", "内容"),
    ])

    mock_llm = MagicMock()
    mock_llm.ainvoke = AsyncMock(side_effect=[
        Exception("LLM error on exp1"),
        MagicMock(content=VALID_JSON),
    ])
    with patch("app.core.agents.extraction_agent.create_llm", return_value=mock_llm):
        await agent.run(context, knowledge_document=bundle)

    kos = context.get_output("knowledge_objects")
    assert len(kos) == 1  # 只有 exp2 成功
    assert kos[0].topic == "实验一"


@pytest.mark.anyio
async def test_group_by_source(agent):
    """按 source 分组"""
    bundle = make_bibli([
        ("doc1.pdf", "a"),
        ("doc2.pdf", "b"),
        ("doc1.pdf", "c"),
    ])
    groups = agent._group_by_source(bundle)
    assert len(groups) == 2
    assert groups[0][0] == "doc1.pdf"
    assert len(groups[0][1]) == 2
    assert groups[1][0] == "doc2.pdf"
    assert len(groups[1][1]) == 1


def make_bibli(items):
    """test helper — 创建带重复 source 的 bundle"""
    chunks = []
    for i, (src, content) in enumerate(items):
        chunks.append(DocumentChunk(source=src, content=content, chunk_index=i))
    return DocumentBundle(chunks=chunks)


def test_parse_output_valid_json(agent):
    """正常 JSON 解析"""
    kos, evs = agent._parse_output(VALID_JSON)
    assert len(kos) == 1
    assert isinstance(kos[0], KnowledgeObject)
    assert kos[0].topic == "实验一"
    assert len(evs) == 1
    assert isinstance(evs[0], Evidence)


def test_parse_output_empty_json(agent):
    """空 JSON"""
    kos, evs = agent._parse_output('{"knowledge_objects": [], "evidence": []}')
    assert len(kos) == 0
    assert len(evs) == 0


def test_parse_output_invalid(agent):
    """无效 JSON 触发 fallback"""
    kos, evs = agent._parse_output("不是JSON")
    assert len(kos) == 0
    # fallback 不会匹配到东西
    assert len(evs) == 0


def test_parse_output_fallback_regex(agent):
    """正则兜底提取 evidence"""
    text = '"statement": "这是一个事实", "source": "doc.pdf"'
    kos, evs = agent._parse_output(text)
    assert len(kos) == 0
    assert len(evs) >= 1
    assert evs[0].statement == '这是一个事实'


def test_extract_sources(agent):
    """从 knowledge_objects 和 evidence 提取 sources"""
    kos = [KnowledgeObject(topic="t1", source="doc1.pdf", attributes={"k": "v"})]
    evs = [Evidence(statement="s1", source="doc2.pdf", evidence_type="text")]
    sources = agent._extract_sources(kos, evs)
    assert len(sources) == 2
    names = [s["file_name"] for s in sources]
    assert "doc1.pdf" in names
    assert "doc2.pdf" in names

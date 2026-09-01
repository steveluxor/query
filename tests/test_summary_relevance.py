from unittest.mock import AsyncMock, MagicMock

import pytest

from app.core.mcp import server


@pytest.mark.anyio
async def test_relevance_uses_original_question_as_the_decision_basis(monkeypatch):
    llm = MagicMock()
    llm.ainvoke = AsyncMock(return_value=MagicMock(content='{"relevant_ids": [2]}'))
    monkeypatch.setattr(server, "llm", llm)

    relevant_ids = await server._judge_relevance(
        original_question="比较 2024 年 A 产品和 B 产品的销售额",
        search_query="销售额对比",
        summaries={2: "包含 2024 年 A 产品销售额", 3: "包含 2023 年销售额"},
    )

    prompt = llm.ainvoke.call_args.args[0]
    assert "原始用户问题" in prompt
    assert "比较 2024 年 A 产品和 B 产品的销售额" in prompt
    assert "改写检索词" in prompt
    assert "销售额对比" in prompt
    assert relevant_ids == {2}


@pytest.mark.anyio
async def test_relevance_keeps_all_documents_when_the_llm_output_is_invalid(monkeypatch):
    llm = MagicMock()
    llm.ainvoke = AsyncMock(return_value=MagicMock(content="not json"))
    monkeypatch.setattr(server, "llm", llm)

    summaries = {2: "A", 3: "B"}
    assert await server._judge_relevance("原始问题", "改写词", summaries) == {2, 3}

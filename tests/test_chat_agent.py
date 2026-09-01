from unittest.mock import MagicMock

import pytest

from app.core.agent_context import AgentContext
from app.core.agents.chat_agent import ChatAgent


@pytest.mark.anyio
async def test_chat_agent_injects_history_and_memory(monkeypatch):
    llm = MagicMock()
    llm.invoke.return_value = MagicMock(content="上一轮讨论的是动态 top_k。")
    monkeypatch.setattr("app.core.infra.llm_factory.create_llm", lambda **kwargs: llm)
    context = AgentContext(
        question="那个是什么意思？",
        history=[{"question": "动态 top_k 是什么？", "answer": "按分数断层选择结果数量。"}],
        memory_context="已知事实：上一轮解释了动态 top_k。",
    )

    await ChatAgent().run(context)

    prompt = llm.invoke.call_args.args[0]
    assert "动态 top_k 是什么？" in prompt
    assert "按分数断层选择结果数量。" in prompt
    assert "已知事实：上一轮解释了动态 top_k。" in prompt
    assert context.get_output("answer") == "上一轮讨论的是动态 top_k。"


def test_chat_history_is_bounded():
    history = [
        {"question": f"问题{i}", "answer": f"回答{i}"}
        for i in range(6)
    ]

    text = ChatAgent._format_history(history)

    assert "问题0" not in text
    assert "问题1" in text
    assert "问题5" in text

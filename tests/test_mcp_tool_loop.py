"""MCP 原生工具调用循环单元测试"""

from unittest.mock import AsyncMock, MagicMock

import pytest
from langchain_core.messages import AIMessage

from app.core.mcp.tool_loop import build_tool_schemas, run_tool_loop


class _MCPTool:
    """模拟 mcp.types.Tool"""

    def __init__(self, name, description, properties, required):
        self.name = name
        self.description = description
        self.inputSchema = {
            "type": "object",
            "properties": properties,
            "required": required,
        }


def _tool(name, required=(), optional=()):
    props = {k: {"type": "string"} for k in required}
    props.update({k: {"type": "string"} for k in optional})
    return _MCPTool(name, f"{name} 描述", props, list(required))


def test_build_tool_schemas_strips_internal_params():
    tool = _tool("calculate_sum",
                 required=("session_id", "key_name"),
                 optional=("task_id", "content_filter", "ctx_source_id", "original_question"))
    schemas = build_tool_schemas([tool])

    assert len(schemas) == 1
    fn = schemas[0]["function"]
    assert fn["name"] == "calculate_sum"
    assert fn["description"] == "calculate_sum 描述"

    params = fn["parameters"]
    assert "session_id" not in params["properties"]
    assert "task_id" not in params["properties"]
    assert "ctx_source_id" not in params["properties"]
    assert "original_question" not in params["properties"]
    assert "key_name" in params["properties"]
    assert params["required"] == ["key_name"]


def test_build_tool_schemas_filters_by_include():
    tools = [
        _tool("read_all_rows", optional=("session_id", "task_id", "ctx_source_id")),
        _tool("calculate_rank", required=("session_id", "key_name", "ascending"),
              optional=("task_id", "ctx_source_id")),
    ]
    schemas = build_tool_schemas(tools, include=["calculate_rank"])
    assert [s["function"]["name"] for s in schemas] == ["calculate_rank"]


class _FakeLLM:
    """依次返回预设响应的假 LLM：先工具调用，再最终答案"""

    def __init__(self, responses):
        self._responses = list(responses)
        self.bound_tools = None

    def bind_tools(self, tools):
        self.bound_tools = tools
        return self

    async def ainvoke(self, messages):
        resp = self._responses.pop(0)
        if isinstance(resp, dict) and resp.get("tool_calls"):
            return AIMessage(content="", tool_calls=resp["tool_calls"])
        return resp


@pytest.mark.anyio
async def test_run_tool_loop_calls_tool_and_returns_final():
    tools = [
        _tool("read_all_rows", optional=("session_id", "task_id", "ctx_source_id")),
        _tool("calculate_sum", required=("session_id", "key_name"),
              optional=("task_id", "ctx_source_id")),
    ]
    mcp = MagicMock()
    mcp.list_tools = AsyncMock(return_value=tools)
    mcp.call_tool = AsyncMock(return_value="求和结果: 5000")

    llm = _FakeLLM([
        {"tool_calls": [{"name": "calculate_sum", "args": {"key_name": "价格"},
                         "id": "call_1", "type": "tool_call"}]},
        AIMessage(content='{"calculations": [], "findings": [], "conclusions": []}'),
    ])

    messages = await run_tool_loop(
        llm, mcp, session_id="s1",
        system_prompt="sys", user_prompt="求价格总和",
        tool_names=["read_all_rows", "calculate_sum"],
    )

    # 绑给 LLM 的 schema 已过滤并剥掉内部参数
    bound_names = [t["function"]["name"] for t in llm.bound_tools]
    assert bound_names == ["read_all_rows", "calculate_sum"]
    sum_fn = next(t["function"] for t in llm.bound_tools
                  if t["function"]["name"] == "calculate_sum")
    assert "session_id" not in sum_fn["parameters"]["properties"]

    # call_tool 收到过滤后的参数 + 注入的 session_id
    mcp.call_tool.assert_awaited_once_with("calculate_sum", {"key_name": "价格"}, session_id="s1")

    # 最终消息是 AI 答案
    final = messages[-1]
    assert final.type == "ai"
    assert "calculations" in final.content


@pytest.mark.anyio
async def test_run_tool_loop_stops_after_max_tool_calls():
    tools = [_tool("calculate_sum", required=("session_id", "key_name"))]
    mcp = MagicMock()
    mcp.list_tools = AsyncMock(return_value=tools)
    mcp.call_tool = AsyncMock(return_value="1")

    tool_call = {"name": "calculate_sum", "args": {"key_name": "价格"},
                 "id": "call_1", "type": "tool_call"}
    llm = _FakeLLM([{"tool_calls": [tool_call]}, {"tool_calls": [tool_call]}])

    await run_tool_loop(
        llm, mcp, session_id="s1",
        system_prompt="sys", user_prompt="求和", tool_names=["calculate_sum"],
        max_tool_calls=1,
    )

    # 达到上限后停止，不再发起新一轮工具执行
    assert mcp.call_tool.await_count == 1

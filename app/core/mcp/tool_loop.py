"""MCP 原生工具调用循环 —— 替代 LangChain create_agent 黑盒。

让 LLM 直接基于 MCP Server 暴露的工具 schema 自主决定调用哪些工具，
代码只负责循环驱动；session_id/task_id/ctx_source_id 等内部参数由
MCPClient.call_tool() 自动注入（见 app/core/mcp/client.py），LLM 不可见。

任何需要"LLM 自主调工具"的 Agent 均可复用本模块，替代
app/core/mcp/tools.py 的 LangChain @tool 包装层。
"""

import logging

from langchain_core.messages import AIMessage, ToolMessage

logger = logging.getLogger(__name__)

# MCPClient.call_tool 自动注入、不暴露给 LLM 的内部参数
_INTERNAL_PARAMS = ("session_id", "task_id", "ctx_source_id")


def build_tool_schemas(mcp_tools, include: list[str] | None = None) -> list[dict]:
    """将 MCP 工具列表转为 OpenAI function-call schema（供 llm.bind_tools 使用）。

    - include: 只保留指定名称的工具（按 Agent 隔离，避免暴露搜索/管理类工具）
    - 从 inputSchema 中剔除 session_id/task_id/ctx_source_id，它们由 client 注入
    """
    schemas = []
    for t in mcp_tools:
        if include and t.name not in include:
            continue
        props = dict(t.inputSchema.get("properties", {}))
        required = list(t.inputSchema.get("required", []))
        for p in _INTERNAL_PARAMS:
            props.pop(p, None)
            if p in required:
                required.remove(p)
        schemas.append({
            "type": "function",
            "function": {
                "name": t.name,
                "description": t.description or "",
                "parameters": {
                    "type": "object",
                    "properties": props,
                    "required": required,
                },
            },
        })
    return schemas


async def run_tool_loop(llm, mcp_client, session_id: str, system_prompt: str,
                        user_prompt: str, tool_names: list[str] | None = None,
                        max_tool_calls: int = 8, max_steps: int = 16) -> list:
    """执行 MCP 原生工具调用循环，返回完整消息列表（含工具结果）。

    1. mcp_client.list_tools() 取 MCP 工具 schema（过滤 + 剥内部参数）
    2. llm.bind_tools(schemas) 绑定
    3. 循环：ainvoke → 有 tool_calls 则逐个 call_tool 并回填 ToolMessage → 否则终止
    """
    mcp_tools = await mcp_client.list_tools()
    schemas = build_tool_schemas(mcp_tools, include=tool_names)
    llm_with_tools = llm.bind_tools(schemas) if schemas else llm

    messages: list = [
        ("system", system_prompt),
        ("human", user_prompt),
    ]
    tool_call_count = 0

    for _ in range(max_steps):
        final = await llm_with_tools.ainvoke(messages)
        messages.append(final)

        tool_calls = getattr(final, "tool_calls", None) or []
        if not tool_calls:
            return messages

        for tc in tool_calls:
            if tool_call_count >= max_tool_calls:
                logger.warning("[ToolLoop] 达到最大工具调用次数 %d，停止", max_tool_calls)
                return messages
            name = tc.get("name")
            args = tc.get("args") or {}
            call_id = tc.get("id") or ""
            try:
                result = await mcp_client.call_tool(name, args, session_id=session_id)
            except Exception as e:
                logger.error("[ToolLoop] 工具 %s 调用失败: %s", name, e)
                result = f"工具调用失败: {e}"
            tool_call_count += 1
            messages.append(ToolMessage(content=str(result), tool_call_id=call_id))

    return messages

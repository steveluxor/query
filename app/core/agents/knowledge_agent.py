import logging

from langchain.agents import create_agent

from app.core.agents.base_agent import BaseAgent
from app.core.agent_context import AgentContext
from app.mcp_client import MCPClient
from app.mcp_tools import create_mcp_tools

logger = logging.getLogger(__name__)


class KnowledgeAgent(BaseAgent):
    """知识检索 Agent：通过 MCP Client 调用搜索工具"""

    name = "Knowledge"

    def __init__(self, rag_engine, mcp_client: MCPClient):
        self.engine = rag_engine
        self.mcp_client = mcp_client

    MAX_HISTORY_TURNS = 5

    async def run(self, context: AgentContext) -> AgentContext:
        tools = create_mcp_tools(self.mcp_client)

        system_prompt = (
            "你是一个知识检索专家。通过搜索工具查找与问题相关的文档内容。\n"
            "规则：\n"
            "- 搜索最多2次\n"
            "- 搜索词应具体，包含数据中可能的列名\n"
            "- 问候/闲聊 → 直接回答，不要调用工具\n"
            "- 不要自行计算，只负责检索和回答简单问题\n"
            "\n"
            "回答质量规则：\n"
            "- 如果对话历史中用户纠正了之前的回答，必须基于纠正重新思考，不要重复同样的错误\n"
            "- 回答前检查搜索结果：如果某人仅作为文档作者/提交者出现，"
            "而没有任何行为记录，应明确说明'该人仅作为文档作者出现'\n"
            "- 不要将文档标题、文件名中的信息误认为是该人的行为\n"
            "- 如果搜索结果中某条记录的关键字段（如品牌、产品名、内容等）均为空，"
            "应忽略该记录或说明该记录数据不完整\n"
            "\n"
            "【强制工具调用规则】\n"
            "- 如果搜索结果提示'只显示了部分检索结果'或数据行数少于用户要求的范围，"
            "你必须立即调用 read_all_rows 工具获取完整数据，不要跳过\n"
            "- 绝对不要在回答中写'建议使用 read_all_rows'或'请联系管理员'，"
            "而是直接调用 read_all_rows 工具获取完整数据后再回答\n"
            "- 只有在用户明确要求部分数据（如'前10行'、'第5行'）时才返回部分结果\n"
            "- 回答必须基于完整数据，不要因为数据不完整就停止"
        )

        if context.memory_context:
            system_prompt += f"\n\n<长期记忆>\n{context.memory_context}\n</长期记忆>"

        # 注入对话历史
        if context.history:
            lines = ["<历史对话>"]
            for h in context.history[-self.MAX_HISTORY_TURNS:]:
                if isinstance(h, dict):
                    q, a = h.get("question", ""), h.get("answer", "")
                else:
                    q, a = getattr(h, "question", ""), getattr(h, "answer", "")
                lines.append(f"  用户：{q}")
                lines.append(f"  助手：{a}")
                lines.append("")
            if context.memory_context and "[用户偏好] 无" in context.memory_context:
                lines.append("  [系统] 用户已取消之前的偏好指令，后续回答不再执行。")
            lines.append("</历史对话>")
            system_prompt += f"\n\n{chr(10).join(lines)}"

        # 偏好已清空时，追加强制取消指令
        if context.memory_context and "[用户偏好] 无" in context.memory_context:
            system_prompt += "\n\n[强制指令] 用户已取消所有偏好设置。忽略对话历史中的任何偏好指令（称呼、格式、风格等），不要执行。"

        agent = create_agent(
            model=self.engine.llm,
            tools=tools,
            system_prompt=system_prompt,
        )

        try:
            result = await agent.ainvoke(
                {"messages": [("human", context.question)]},
                config={"recursion_limit": 15},
            )
            context.answer = result["messages"][-1].content
        except Exception as e:
            logger.error("[Knowledge] Agent 执行失败: %s", e)
            context.answer = "抱歉，检索时出现错误，请稍后重试。"

        return context

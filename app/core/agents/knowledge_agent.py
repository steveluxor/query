import json
import logging

from langchain.agents import create_agent
from langchain_core.messages import HumanMessage

from app.core.agents.base_agent import BaseAgent
from app.core.agent_context import AgentContext
from app.core.rag_engine import RAGEngine
from app.core.mcp.client import MCPClient
from app.core.mcp.tools import create_mcp_tools
from app.models.data_types import Evidence

logger = logging.getLogger(__name__)

KNOWLEDGE_SYSTEM_PROMPT = """你是一个知识检索专家。通过搜索工具查找与问题相关的文档内容。

工具：
- search_documents: 搜索文档内容
- read_all_rows: 读取搜索到的文档的全部数据行

规则：
- 搜索最多2次
- 搜索词应具体，包含数据中可能的列名
- 问候/闲聊 → 不要调用工具，直接返回空 evidence
- 如果搜索结果提示数据不完整，调用 read_all_rows
- 不要自行计算，只负责检索

你必须以 JSON 格式输出，不要输出任何自然语言。
如果无法提取证据，返回 {"evidence": []}。

输出格式：
{
  "evidence": [
    {
      "statement": "事实陈述",
      "source": "文件名",
      "evidence_type": "table",
      "metadata": {}
    }
  ]
}

evidence_type 取值：
- "table": 表格数据中的行/列
- "text": 文本段落
- "calculation": 工具计算结果"""


class KnowledgeAgent(BaseAgent):
    """知识检索 Agent：检索文档并提取结构化 Evidence"""

    name = "Knowledge"

    def __init__(self, rag_engine: RAGEngine, mcp_client: MCPClient):
        self.engine = rag_engine
        self.mcp_client = mcp_client

    MAX_HISTORY_TURNS = 5

    async def run(self, context: AgentContext) -> AgentContext:
        tools = create_mcp_tools(self.mcp_client, include=["search_documents", "list_documents", "read_all_rows"])

        system_prompt = KNOWLEDGE_SYSTEM_PROMPT

        if context.memory_context:
            system_prompt += f"\n\n<长期记忆>\n{context.memory_context}\n</长期记忆>"

        if context.history:
            lines = ["<历史对话>"]
            for h in context.history[-self.MAX_HISTORY_TURNS:]:
                if isinstance(h, dict):
                    q, a = h.get("question", ""), h.get("answer", "")
                else:
                    q, a = getattr(h, "question", ""), getattr(h, "answer", "")
                lines.append(f"  用户：{q}")
                lines.append(f"  助手：{a}")
            lines.append("</历史对话>")
            system_prompt += f"\n\n{chr(10).join(lines)}"

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

            # 保存原始工具调用结果供 extract_evidence 使用
            raw_messages = result["messages"]
            raw_tool_results = self._extract_tool_results(raw_messages)

            # 从 LLM 最终输出中提取 Evidence
            evidence = self._parse_evidence(raw_messages)
            context.set_evidence(evidence)

            # 提取 sources
            sources = self._extract_sources(raw_tool_results, evidence)
            context.set_sources(sources)

            logger.info("[Knowledge] 提取 %d 条 Evidence", len(evidence))

        except Exception as e:
            logger.error("[Knowledge] Agent 执行失败: %s", e)
            context.evidence = []
            context.sources = []

        return context

    def _extract_tool_results(self, messages) -> list[str]:
        """从消息列表中提取所有工具返回的文本"""
        results = []
        for msg in messages:
            if hasattr(msg, "type") and msg.type == "tool":
                results.append(msg.content)
        return results

    def _parse_evidence(self, messages) -> list[Evidence]:
        """从 LLM 最终输出中解析 Evidence JSON"""
        # 找到最后一条 AI 消息（非 tool call）
        logger.warning("[Knowledge] 消息总数: %d", len(messages))
        for i, msg in enumerate(messages):
            logger.warning("[Knowledge]   msg[%d] type=%s, has_tool_calls=%s, content_len=%d",
                           i, getattr(msg, "type", "?"),
                           bool(getattr(msg, "tool_calls", None)),
                           len(getattr(msg, "content", "")))
        for msg in reversed(messages):
            if hasattr(msg, "type") and msg.type == "ai" and not getattr(msg, "tool_calls", None):
                content = msg.content
                return self._extract_evidence_from_text(content)
        return []

    def _extract_evidence_from_text(self, text: str) -> list[Evidence]:
        """从文本中提取 Evidence（兼容 markdown 代码块）"""
        logger.warning("[Knowledge] LLM 原始输出 (前500字符): %s", text[:500])
        try:
            # 尝试直接解析
            data = json.loads(text)
        except json.JSONDecodeError:
            # 尝试从 markdown 代码块中提取
            if "```" in text:
                parts = text.split("```")
                for part in parts:
                    part = part.strip()
                    if part.startswith("json"):
                        part = part[4:]
                    try:
                        data = json.loads(part.strip())
                        break
                    except json.JSONDecodeError:
                        continue
                else:
                    logger.warning("[Knowledge] 无法解析 Evidence JSON")
                    return []
            else:
                # 尝试找到 JSON 块
                start = text.find("{")
                end = text.rfind("}")
                if start != -1 and end > start:
                    try:
                        data = json.loads(text[start:end + 1])
                    except json.JSONDecodeError:
                        logger.warning("[Knowledge] 无法解析 Evidence JSON")
                        return []
                else:
                    return []

        raw_evidence = data.get("evidence", [])
        result = []
        for item in raw_evidence:
            if isinstance(item, dict) and "statement" in item:
                result.append(Evidence(
                    statement=item.get("statement", ""),
                    source=item.get("source", ""),
                    evidence_type=item.get("evidence_type", "text"),
                    metadata=item.get("metadata", {}),
                ))
        return result

    def _extract_sources(self, tool_results: list[str], evidence: list[Evidence]) -> list[dict]:
        """从工具结果和 evidence 中提取来源信息"""
        sources = {}
        for ev in evidence:
            if ev.source and ev.source not in sources:
                sources[ev.source] = {
                    "file_name": ev.source,
                    "content": ev.statement[:200],
                }
        return list(sources.values())

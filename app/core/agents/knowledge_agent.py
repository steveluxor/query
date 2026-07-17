import json
import logging
import re

from langchain.agents import create_agent
from langchain_core.messages import HumanMessage

from app.core.agents.base_agent import BaseAgent
from app.core.agent_context import AgentContext
from app.core.rag_engine import RAGEngine
from app.core.mcp.client import MCPClient
from app.core.mcp.tools import create_mcp_tools
from app.core.prompt_manager import PromptManager
from app.models.data_types import Evidence, RetrievalReport
from app.models.capability import AgentCapability, AgentRole

logger = logging.getLogger(__name__)


class KnowledgeAgent(BaseAgent):
    """知识检索 Agent：检索文档并提取结构化 Evidence"""

    name = "Knowledge"
    capability = AgentCapability(
        name="knowledge",
        description="知识检索，搜索文档、提取事实",
        inputs=[],
        outputs={
            "evidence": list[Evidence],
            "sources": list[dict],
            "retrieval_report": RetrievalReport,
        },
        tools=["search_documents", "list_documents", "read_all_rows"],
        merge_policy={
            "evidence": "dedup",
            "sources": "dedup",
        },
        role=AgentRole.EXECUTOR,
    )

    def __init__(self, rag_engine: RAGEngine):
        self.engine = rag_engine

    MAX_HISTORY_TURNS = 5

    async def run(self, context: AgentContext, mcp_client: MCPClient = None, mcp_session_id: str = "") -> AgentContext:
        tools = create_mcp_tools(mcp_client, session_id=mcp_session_id, include=["search_documents", "list_documents", "read_all_rows"])

        system_prompt = PromptManager.get("knowledge", "system")

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
            context.set_output("evidence", evidence, producer="knowledge")

            # 提取 sources
            sources = self._extract_sources(raw_tool_results, evidence)
            context.set_output("sources", sources, producer="knowledge")

            # 提取 retrieval_report（证据完整性报告）
            report = self._extract_retrieval_report(raw_messages, raw_tool_results)
            context.set_output("retrieval_report", report, producer="knowledge")

            logger.info("[Knowledge] 提取 %d 条 Evidence, retrieval_report: is_complete=%s",
                        len(evidence), report.is_complete)

        except Exception as e:
            logger.error("[Knowledge] Agent 执行失败: %s", e)
            context.set_output("evidence", [], producer="knowledge")
            context.set_output("sources", [], producer="knowledge")

        return context

    def _extract_tool_results(self, messages) -> list[str]:
        """从消息列表中提取所有工具返回的文本"""
        results = []
        for msg in messages:
            if hasattr(msg, "type") and msg.type == "tool":
                results.append(msg.content)
        return results

    def _parse_evidence(self, messages) -> list[Evidence]:
        """从 LLM 最终输出或工具结果中解析 Evidence"""
        logger.warning("[Knowledge] 消息总数: %d", len(messages))
        for i, msg in enumerate(messages):
            logger.warning("[Knowledge]   msg[%d] type=%s, has_tool_calls=%s, content_len=%d",
                           i, getattr(msg, "type", "?"),
                           bool(getattr(msg, "tool_calls", None)),
                           len(getattr(msg, "content", "")))

        # 1. 优先尝试 LLM 最终输出的 JSON
        for msg in reversed(messages):
            if hasattr(msg, "type") and msg.type == "ai" and not getattr(msg, "tool_calls", None):
                content = msg.content
                evidence = self._extract_evidence_from_text(content)
                if evidence:
                    return evidence
                break  # 找到了最终 AI 消息但无法解析，走兜底

        # 2. 兜底：直接从工具结果中提取证据（不依赖 LLM 的 JSON 输出）
        tool_results = self._extract_tool_results(messages)
        evidence = self._extract_evidence_from_tool_results(tool_results)
        if evidence:
            logger.warning("[Knowledge] 从工具结果直接提取到 %d 条 Evidence (兜底)", len(evidence))
            return evidence

        return []

    def _extract_evidence_from_text(self, text: str) -> list[Evidence]:
        """从文本中提取 Evidence（兼容 markdown 代码块 + 断裂 JSON 兜底）"""
        logger.warning("[Knowledge] LLM 原始输出 (前500字符): %s", text[:500])
        from app.core.utils import extract_json
        data = extract_json(text)
        if data is None:
            logger.warning("[Knowledge] extract_json 失败，尝试正则兜底提取")
            return self._extract_evidence_fallback(text)

        raw_evidence = data.get("evidence", []) if isinstance(data, dict) else []
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

    def _extract_evidence_from_tool_results(self, tool_results: list[str]) -> list[Evidence]:
        """从 search_documents 工具结果中直接提取 Evidence（不依赖 LLM 的 JSON 输出能力）

        工具返回格式（MCP search_documents）：
          {"data": "检索到以下相关内容：\n\n[文件名]\n内容..."}
        或纯文本格式：
          检索到以下相关内容：\n\n[文件名]\n内容...
        """
        evidence = []
        seen_sources = set()

        for text in tool_results:
            chunks_text = text

            # MCP 包装的 JSON 格式
            try:
                data = json.loads(text)
                if isinstance(data, dict) and "data" in data:
                    chunks_text = data["data"]
            except (json.JSONDecodeError, TypeError):
                pass

            if not any(prefix in chunks_text for prefix in ("检索到以下相关内容", "以下是完整数据")):
                continue

            # 用 [文件名] 或 [文件名 / sheet名] 标记分割
            # 先在每段前加换行方便统一处理
            normalized = chunks_text.replace("\r\n", "\n")
            segments = re.split(r'\n(?=\[)', normalized)

            for seg in segments:
                m = re.match(r'^\[(.+?)\]\s*\n(.*)', seg, re.DOTALL)
                if m:
                    source = m.group(1).strip()
                    content = m.group(2).strip()
                    # 去掉末尾的系统提示（如"【重要】以上只显示了部分数据..."）
                    content = re.sub(r'\n\n【重要】.*', '', content, flags=re.DOTALL).strip()
                    dedup_key = (source, content[:200])
                    if content and dedup_key not in seen_sources:
                        seen_sources.add(dedup_key)
                        evidence.append(Evidence(
                            statement=content,
                            source=source,
                            evidence_type="text",
                        ))

        return evidence

    def _extract_evidence_fallback(self, text: str) -> list[Evidence]:
        """当 JSON 解析完全失败时，用正则从原始文本中提取 evidence 字段"""
        results = []

        stmt_pattern = re.compile(
            r'"statement"\s*:\s*"((?:(?!",\s*"(?:source|evidence_type|metadata)).)+)"'
        )
        statements = stmt_pattern.findall(text)

        src_pattern = re.compile(
            r'"source"\s*:\s*"((?:(?!",\s*"(?:source|evidence_type|metadata)).)+)"'
        )
        sources = src_pattern.findall(text)

        for i, statement in enumerate(statements):
            source = sources[i] if i < len(sources) else ""
            results.append(Evidence(
                statement=statement.strip(),
                source=source.strip(),
                evidence_type="text",
            ))

        if results:
            logger.warning("[Knowledge] 正则兜底提取到 %d 条 Evidence", len(results))

        return results

    def _extract_retrieval_report(self, messages, tool_results: list[str]) -> RetrievalReport:
        """从 LLM 输出和工具调用结果中提取检索完整性报告"""
        # 1. 尝试从 LLM 最终 JSON 输出中提取 retrieval_report
        for msg in reversed(messages):
            if hasattr(msg, "type") and msg.type == "ai" and not getattr(msg, "tool_calls", None):
                from app.core.utils import extract_json
                data = extract_json(msg.content)
                if isinstance(data, dict) and "retrieval_report" in data:
                    rr = data["retrieval_report"]
                    return RetrievalReport(
                        sources=rr.get("sources", []),
                        total_chunks=rr.get("total_chunks", 0),
                        returned_chunks=rr.get("returned_chunks", 0),
                        is_complete=rr.get("is_complete", False),
                        read_all_rows_called=rr.get("read_all_rows_called", False),
                        searches_performed=rr.get("searches_performed", 0),
                    )
                break

        # 2. 兜底：从工具调用轨迹推断
        read_all_rows_called = any(
            "read_all_rows" in str(getattr(msg, "tool_calls", []))
            for msg in messages
        )
        search_count = sum(
            1 for msg in messages
            if hasattr(msg, "tool_calls") and msg.tool_calls
            and any(tc.get("name") == "search_documents" for tc in (msg.tool_calls or []))
        )
        sources = list(dict.fromkeys(
            self._extract_sources(tool_results, [])
        ))

        total_chunks = 0
        returned_chunks = 0
        for text in tool_results:
            try:
                data = json.loads(text)
                if isinstance(data, dict):
                    returned_chunks = data.get("rows_returned", returned_chunks)
                    if data.get("total_chunks"):
                        total_chunks = data["total_chunks"]
                    elif data.get("is_complete") is False:
                        total_chunks = max(total_chunks, returned_chunks + 1)
            except (json.JSONDecodeError, TypeError):
                pass

        logger.info("[Knowledge] 兜底生成 retrieval_report: search=%d次, read_all_rows=%s, sources=%s",
                    search_count, read_all_rows_called, sources)
        return RetrievalReport(
            sources=sources,
            total_chunks=total_chunks,
            returned_chunks=returned_chunks,
            is_complete=read_all_rows_called or search_count == 0,
            read_all_rows_called=read_all_rows_called,
            searches_performed=search_count,
        )

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

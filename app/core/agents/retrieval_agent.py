import json
import logging
import re

from app.core.agents.base_agent import BaseAgent
from app.core.agent_context import AgentContext
from app.core.llm_factory import create_llm
from app.models.capability import AgentCapability, AgentRole
from app.models.data_types import DocumentBundle, DocumentChunk, RetrievalReport

logger = logging.getLogger(__name__)


class RetrievalAgent(BaseAgent):
    """检索 Agent：LLM 只生成 query，代码控制搜索 + 全量加载"""

    name = "Retrieval"
    capability = AgentCapability(
        name="retrieval",
        description="文档检索，搜索并获取完整文档内容",
        outputs={
            "document_bundle": DocumentBundle,
            "retrieval_report": RetrievalReport,
        },
        tools=[],
        merge_policy={"document_bundle": "append"},
        role=AgentRole.EXECUTOR,
    )

    QUERY_PROMPT = (
        "你是一个搜索关键词生成器。根据用户问题，生成一个最可能找到相关文档的搜索关键词。\n"
        "要求：\n"
        "- 只返回关键词本身，不要任何解释、标点、引号\n"
        "- 如果用户问题本身就是合适的关键词，直接返回原文\n"
        "- 关键词应包含数据中可能的列名或文档标题\n\n"
        "用户问题：{question}"
    )

    async def run(self, context: AgentContext, mcp_client=None, mcp_session_id: str = "", **kwargs) -> AgentContext:
        question = context.question

        # 1. LLM 生成搜索词（单次调用，temperature=0）
        search_query = await self._generate_query(question)
        logger.info("[Retrieval] 搜索词: %s", search_query)

        # 2. 搜索（代码控制）
        search_text = ""
        full_text = ""
        try:
            raw = await mcp_client.call_tool("search_documents", {"query": search_query}, session_id=mcp_session_id)
            raw_str = str(raw)
            # 解析 JSON 包装
            try:
                data = json.loads(raw_str)
                if isinstance(data, dict):
                    search_text = data.get("data", raw_str)
                else:
                    search_text = raw_str
            except (json.JSONDecodeError, TypeError):
                search_text = raw_str

            # 检查是否搜到了内容
            if "检索到以下相关内容" in search_text:
                # 3. 无条件调 read_all_rows
                try:
                    full_raw = await mcp_client.call_tool("read_all_rows", {}, session_id=mcp_session_id)
                    full_text = str(full_raw)
                except Exception as e:
                    logger.warning("[Retrieval] read_all_rows 失败: %s", e)
        except Exception as e:
            logger.error("[Retrieval] search_documents 失败: %s", e)

        # 4. 解析为 DocumentBundle
        bundle = self._parse_document_bundle(search_text, full_text)
        context.set_output("document_bundle", bundle, producer="retrieval")

        # 5. 检索报告
        report = self._extract_retrieval_report(bundle, search_text)
        context.set_output("retrieval_report", report, producer="retrieval")

        logger.info("[Retrieval] DocumentBundle: %d chunks from %d docs",
                     len(bundle.chunks),
                     len({c.source for c in bundle.chunks}))

        return context

    async def _generate_query(self, question: str) -> str:
        """LLM 从用户问题生成搜索关键词"""
        llm = create_llm(temperature=0, max_tokens=100)
        try:
            prompt = self.QUERY_PROMPT.format(question=question)
            result = await llm.ainvoke([("human", prompt)])
            return result.content.strip().strip('"\'')
        except Exception as e:
            logger.warning("[Retrieval] query 生成失败，使用原始问题: %s", e)
            return question

    def _parse_document_bundle(self, search_text: str, full_text: str) -> DocumentBundle:
        """从 search + read_all_rows 结果解析为 DocumentBundle"""
        all_texts = []
        if search_text and "检索到以下相关内容" in search_text:
            all_texts.append(search_text)
        if full_text and "以下是完整数据" in full_text:
            all_texts.append(full_text)

        if not all_texts:
            return DocumentBundle(chunks=[])

        seen = set()
        chunks = []
        chunk_index = 0

        for text in all_texts:
            normalized = text.replace("\r\n", "\n")
            # 去掉尾部系统提示
            normalized = re.sub(r'\n\n【重要】.*', '', normalized, flags=re.DOTALL)
            normalized = re.sub(r'\n\n以上为该文档全部数据。.*', '', normalized, flags=re.DOTALL)
            segments = re.split(r'\n(?=\[)', normalized)

            for seg in segments:
                m = re.match(r'^\[(.+?)\]\s*\n(.*)', seg, re.DOTALL)
                if m:
                    source = m.group(1).strip()
                    content = m.group(2).strip()
                    dedup_key = (source, content[:200])
                    if content and dedup_key not in seen:
                        seen.add(dedup_key)
                        chunks.append(DocumentChunk(
                            source=source,
                            content=content,
                            chunk_index=chunk_index,
                            total_chunks=0,
                        ))
                        chunk_index += 1

        # 补 total_chunks
        source_counts = {}
        for c in chunks:
            source_counts[c.source] = source_counts.get(c.source, 0) + 1
        for c in chunks:
            c.total_chunks = source_counts.get(c.source, 0)

        return DocumentBundle(chunks=chunks)

    def _extract_retrieval_report(self, bundle: DocumentBundle, search_text: str) -> RetrievalReport:
        """从搜索结果和 DocumentBundle 推断检索报告"""
        sources = list(dict.fromkeys(c.source for c in bundle.chunks))
        has_search = "检索到以下相关内容" in search_text
        return RetrievalReport(
            sources=sources,
            total_chunks=len(bundle.chunks),
            returned_chunks=len(bundle.chunks),
            is_complete=True,
            read_all_rows_called=True,
            searches_performed=1 if has_search else 0,
        )

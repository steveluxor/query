import json
import logging
import re

from app.core.agents.base_agent import BaseAgent
from app.core.agent_context import AgentContext
from app.core.llm_factory import create_llm
from app.models.capability import AgentCapability
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
    )

    QUERY_PROMPT = (
        "你是一个搜索策略分析器。根据用户问题判断查询类型并生成搜索关键词。\n\n"
        '"aggregation" — 只用于明确的数值求和(总共/合计/总计)、排名(最贵/最便宜/第N)、'
        "行号范围(第X到Y行/前N行)。需要单个文档的数据。\n"
        "  例如：\"2024总销售额\"、\"最贵的商品\"、\"第100到120行\"、\"前10名\"\n\n"
        '"comparison" — 除 aggregation 外的所有其他问题。包括对比差异、查询某人/事的信息、'
        "总结归纳、列举文档等。需要多个文档的数据。\n"
        "  例如：\"对比一下几个实验\"、\"汪小丹干了什么\"、\"总结所有实验\"、\"有哪些文档\"\n\n"
        "注意：不确认时一律选 comparison。只有明确是数值加总或排序才选 aggregation。\n\n"
        "返回 JSON：\n"
        '{{"type": "aggregation", "query": "关键词"}}\n'
        '{{"type": "comparison", "query": "关键词"}}\n\n'
        "用户问题：{question}"
    )

    async def run(self, context: AgentContext, mcp_client=None, mcp_session_id: str = "", **kwargs) -> AgentContext:
        question = context.question

        # 1. LLM 生成搜索词 + 查询类型（单次调用，temperature=0）
        search_query, query_type = await self._generate_query(question)
        logger.info("[Retrieval] 搜索词: %s, 类型: %s", search_query, query_type)

        # 2. 按策略搜索（aggregation→strict 单文档, comparison→standard 多文档）
        strategy = "strict" if query_type == "aggregation" else "standard"
        search_text = ""
        full_text = ""
        try:
            raw = await mcp_client.call_tool("search_documents", {"query": search_query, "strategy": strategy}, session_id=mcp_session_id)
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

    async def _generate_query(self, question: str) -> tuple[str, str]:
        """LLM 从用户问题生成搜索关键词和查询类型"""
        llm = create_llm(temperature=0, max_tokens=200)
        try:
            prompt = self.QUERY_PROMPT.format(question=question)
            result = await llm.ainvoke([("human", prompt)])
            raw = result.content.strip()
            try:
                data = json.loads(raw)
                query = data.get("query", question).strip('"\'')
                query_type = data.get("type", "comparison")
                return query, query_type
            except (json.JSONDecodeError, TypeError):
                return raw.strip('"\''), "comparison"
        except Exception as e:
            logger.warning("[Retrieval] query 生成失败，使用原始问题: %s", e)
            return question, "comparison"

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

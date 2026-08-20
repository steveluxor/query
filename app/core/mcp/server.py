import asyncio
import json
import logging

from mcp.server.fastmcp import FastMCP

from app.config import settings
from app.core.infra.vector_store import VectorStore
from app.core.infra.summary_cache import DocumentSummaryCache
from app.core.rag_engine import RAGEngine, SearchContext
from app.core.mcp.session_manager import SessionManager
from app.core.prompts.prompt_manager import PromptManager

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

mcp = FastMCP("rag-tools")

# 全局实例（启动时初始化）
rag_engine: RAGEngine = None
summary_cache: DocumentSummaryCache = None
llm = None
session_mgr = SessionManager()


def _get_search_ctx(session, task_id: str, ctx_source_id: str = ""):
    """获取 task 隔离的 SearchContext，查找顺序：
    1. ctx_source_id（消费者工具锁定的上游"检索提供者"，并行分支 DAG 下隔离正确性）
    2. 自身 task_id 的搜索上下文
    3. 共享 search_ctx（串行链式场景：analysis 复用上游 retrieval 的结果）

    （防御性：当前 planner 单检索 DAG 下 1/2 级恒命中同一 ctx，等价于 3 级共享 search_ctx。）"""
    if ctx_source_id and session.search_contexts.get(ctx_source_id):
        return session.search_contexts[ctx_source_id]
    if task_id and session.search_contexts.get(task_id):
        return session.search_contexts[task_id]
    return session.search_ctx


@mcp.tool()
async def _create_session(session_id: str) -> str:
    """内部 tool：创建 MCP session。由 MCPClient.create_session() 调用，不暴露给 Agent。"""
    await session_mgr.create(session_id)
    return session_id


@mcp.tool()
async def set_document_ids(session_id: str, ids: list[int]) -> str:
    """设置当前用户有权限访问的文档 ID 列表。在搜索前必须调用。"""
    session = await session_mgr.get(session_id)
    session.document_ids = ids
    logger.info("[MCP] set_document_ids (session=%s): %d 个文档", session_id[:8], len(ids))
    return f"已设置 {len(ids)} 个可访问文档"


@mcp.tool()
async def search_documents(session_id: str, query: str, strategy: str = "standard",
                           row_start: int | None = None, row_end: int | None = None,
                           task_id: str = "", ctx_source_id: str = "") -> str:
    """从知识库中搜索与问题相关的文档内容。需要查找具体信息、数据、记录时调用。搜索词应具体，包含数据中可能的列名。
    如果要查询特定行号范围（如"第90到100行"、"第91行之后"），请传入 row_start 和 row_end 参数。"""
    logger.info("[MCP] search_documents (session=%s, task=%s): query='%s', strategy=%s, row_start=%s, row_end=%s",
                session_id[:8], task_id or "-", query, strategy, row_start, row_end)

    session = await session_mgr.get(session_id)
    ctx = SearchContext(document_ids=session.document_ids)
    raw_result = rag_engine._execute_search(query, row_start, row_end, ctx, strategy=strategy)

    # 缓存状态到 per-session
    session.search_ctx = ctx
    if task_id:
        session.search_contexts[task_id] = ctx

    # 解析结果，提取数据完整性信息
    rows_returned = len(ctx.last_search_chunks) if ctx.last_search_chunks else 0
    is_complete = "以上只显示了部分数据" not in raw_result if ctx.last_search_chunks else True
    available_actions = ["read_all_rows"] if not is_complete else []

    result = {
        "rows_returned": rows_returned,
        "is_complete": is_complete,
        "available_actions": available_actions,
        "data": raw_result,
    }

    # 摘要过滤：用 LLM 判断文档相关性
    # 旧逻辑依赖 "[文件: ...]" header 行匹配 doc_id，但 _execute_search 实际输出 "[{file_name} / {sheet}]"，
    # 导致 doc_id 提取几乎总为空，过滤块形同虚设。改为直接从搜索结果 chunk 的 metadata 提取 document_id。
    if summary_cache and raw_result:
        try:
            chunks = ctx.last_search_chunks or []
            doc_ids = {
                doc.metadata.get("document_id")
                for doc, _ in chunks
                if doc.metadata.get("document_id") is not None
            }

            if doc_ids:
                summaries = await summary_cache.get_batch(list(doc_ids))
                if summaries:
                    relevant_ids = await _judge_relevance(query, summaries)
                    # 安全阀：过滤比例过高时跳过（可能误判）
                    if len(relevant_ids) < len(doc_ids) / 2:
                        logger.info("[MCP] 摘要过滤过于激进 (%d/%d)，跳过过滤",
                                     len(relevant_ids), len(doc_ids))
                    else:
                        filtered_chunks = [
                            (doc, score) for doc, score in chunks
                            if doc.metadata.get("document_id") in relevant_ids
                        ]
                        if filtered_chunks and len(filtered_chunks) != len(chunks):
                            # 写回 search context：让 read_all_rows/calculate_* 等下游工具只看到相关文档，
                            # 否则无关文档（如账单）会全量泄漏给 analysis/code
                            ctx.last_search_chunks = filtered_chunks
                            ctx.last_search_all_chunks = []   # 使 _load_all_chunks 的惰性缓存失效，强制重载
                            # 按与 _execute_search 一致的格式重建过滤后的文本
                            context_parts = []
                            for doc, _ in filtered_chunks:
                                source_name = doc.metadata.get("file_name", "未知文档")
                                sheet_name = doc.metadata.get("sheet_name")
                                label = f"{source_name} / {sheet_name}" if sheet_name else source_name
                                context_parts.append(f"[{label}]\n{doc.page_content}")
                            filtered_text = "检索到以下相关内容：\n\n" + "\n\n".join(context_parts)
                            # 保留数据不完整提示（read_all_rows 触发条件）
                            if "只显示了部分数据" in raw_result:
                                filtered_text += (
                                    "\n\n【重要】以上只显示了部分数据。"
                                    "你必须立即调用 read_all_rows 工具获取完整数据，不要跳过此步骤。"
                                    "在获取完整数据之前，不要生成最终回答。"
                                )
                            result["data"] = filtered_text
                            result["filtered_by_summary"] = True
                            logger.info("[MCP] 摘要过滤: %d -> %d 个文档", len(doc_ids), len(relevant_ids))
        except Exception as e:
            logger.error("[MCP] 摘要过滤失败，使用原始结果: %s", e, exc_info=True)

    return json.dumps(result, ensure_ascii=False)


@mcp.tool()
async def list_documents(session_id: str) -> str:
    """列出当前知识库中可检索的文档数量和名称。当用户问"有多少文件"、"能搜到几个文档"、"有哪些文档"等元信息问题时调用。"""
    logger.info("[MCP] list_documents (session=%s)", session_id[:8])

    session = await session_mgr.get(session_id)
    all_names = rag_engine.vector_store.get_document_names()
    if session.document_ids:
        matched = {did: all_names[did] for did in session.document_ids if did in all_names}
    else:
        matched = all_names

    if not matched:
        return "当前知识库中没有可检索的文档。"

    lines = [f"共 {len(matched)} 个文档："]
    for did, name in sorted(matched.items()):
        lines.append(f"- [{did}] {name}")
    return "\n".join(lines)


@mcp.tool()
async def calculate_sum(session_id: str, key_name: str, row_filter: str = "", content_filter: str = "",
                        task_id: str = "", ctx_source_id: str = "") -> str:
    """对已检索到的文档内容中指定列（key）的数值进行精确求和。当用户问"总共"、"合计"、"一共多少钱"等加总问题时调用。必须先调用 search_documents 获取数据后才能使用此工具。
    content_filter: 可选，按内容过滤，格式为"列名=值"，如"品牌=万代"只对品牌为万代的行求和。"""
    logger.info("[MCP] calculate_sum (session=%s, task=%s): key_name='%s', row_filter='%s', content_filter='%s'",
                session_id[:8], task_id or "-", key_name, row_filter, content_filter)

    session = await session_mgr.get(session_id)
    ctx = _get_search_ctx(session, task_id, ctx_source_id)
    if not ctx:
        return "请先调用 search_documents 搜索数据。"

    return rag_engine._execute_sum(key_name, row_filter, content_filter, ctx)


@mcp.tool()
async def calculate_rank(session_id: str, key_name: str, ascending: bool, position: int = 1, content_filter: str = "",
                         task_id: str = "", ctx_source_id: str = "") -> str:
    """从已检索到的文档内容中，对指定列（key）的数值排序并返回第N名的记录。当用户问"最贵"、"最便宜"、"第三高"等排名问题时调用。ascending=true=升序(最便宜/最低)，false=降序(最贵/最高)。必须先调用 search_documents 获取数据后才能使用此工具。
    content_filter: 可选，按内容过滤，格式为"列名=值"，如"品牌=万代"只对品牌为万代的记录排序。"""
    logger.info("[MCP] calculate_rank (session=%s, task=%s): key_name='%s', ascending=%s, position=%d",
                session_id[:8], task_id or "-", key_name, ascending, position)

    session = await session_mgr.get(session_id)
    ctx = _get_search_ctx(session, task_id, ctx_source_id)
    if not ctx:
        return "请先调用 search_documents 搜索数据。"

    return rag_engine._execute_rank(key_name, ascending, position, content_filter, ctx)


@mcp.tool()
async def read_all_rows(session_id: str, task_id: str = "", ctx_source_id: str = "") -> str:
    """读取当前搜索到的文档的全部内容。当需要所有章节、所有记录、完整文本时调用。适用于所有文档类型（Word、Excel、PDF 等）。search_documents 只返回部分数据片段，调用此工具可获取全文。必须先调用 search_documents 才能使用。"""
    logger.info("[MCP] read_all_rows (session=%s, task=%s)", session_id[:8], task_id or "-")

    session = await session_mgr.get(session_id)
    ctx = _get_search_ctx(session, task_id, ctx_source_id)
    if not ctx:
        return "请先调用 search_documents 搜索数据。"

    return rag_engine._execute_read_all_rows(ctx)


@mcp.tool()
async def add_documents(session_id: str, document_id: int, texts: list[str], metadatas: list[dict]) -> str:
    """向知识库添加文档切片（ingestion 专用）。先删除旧向量，再写入新切片。"""
    logger.info("[MCP] add_documents (session=%s): document_id=%d, chunks=%d", session_id[:8], document_id, len(texts))
    rag_engine.vector_store.delete_by_document_id(document_id)
    rag_engine.vector_store.add_texts(texts, metadatas)
    return f"已添加 {len(texts)} 个切片"


@mcp.tool()
async def delete_document(session_id: str, document_id: int) -> str:
    """从知识库中删除指定文档的所有切片。"""
    logger.info("[MCP] delete_document (session=%s): document_id=%d", session_id[:8], document_id)
    rag_engine.vector_store.delete_by_document_id(document_id)
    return f"已删除文档 {document_id}"


@mcp.tool()
async def _cleanup_session(session_id: str) -> str:
    """内部 tool：清理 session。由 MCPClient.cleanup_session() 调用，不暴露给 Agent。"""
    await session_mgr.delete(session_id)
    logger.info("[MCP] _cleanup_session (session=%s)", session_id[:8])
    return "已清理"


async def _judge_relevance(query: str, summaries: dict[int, str]) -> set[int]:
    """LLM 批量判断文档相关性"""
    if not summaries:
        return set(summaries.keys())

    summaries_text = "\n".join(
        f"文档{did}: {summary}" for did, summary in summaries.items()
    )

    prompt = PromptManager.get("relevance", "judge").format(
        question=query, summaries=summaries_text
    )
    response = await llm.ainvoke(prompt)

    try:
        result = json.loads(response.content)
        return set(result.get("relevant_ids", summaries.keys()))
    except Exception:
        return set(summaries.keys())


async def main():
    """启动 MCP Server（stdio 模式）"""
    global rag_engine, summary_cache, llm

    # 初始化向量数据库和 RAG 引擎
    vs = VectorStore()
    rag_engine = RAGEngine(vs)

    # 初始化摘要缓存
    summary_cache = DocumentSummaryCache(
        settings.java_base_url,
        settings.resolved_internal_service_token,
    )

    # 初始化 LLM（用于相关性判断）
    from app.core.infra.llm_factory import create_llm
    llm = create_llm()

    # 加载提示词
    PromptManager.initialize()

    logger.info("[MCP Server] 初始化完成，等待连接...")

    await mcp.run_stdio_async()


if __name__ == "__main__":
    asyncio.run(main())

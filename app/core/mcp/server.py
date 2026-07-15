import asyncio
import json
import logging

from mcp.server.fastmcp import FastMCP

from app.config import settings
from app.core.vector_store import VectorStore
from app.core.rag_engine import RAGEngine, SearchContext

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

mcp = FastMCP("rag-tools")

# 全局实例（启动时初始化）
rag_engine: RAGEngine = None


# 状态管理：在 MCP Server 内部维护
class MCPState:
    """工具执行状态，供 search_documents 和 calculate 工具共享"""

    search_ctx: SearchContext = None
    document_ids: list[int] = None


@mcp.tool()
async def set_document_ids(ids: list[int]) -> str:
    """设置当前用户有权限访问的文档 ID 列表。在搜索前必须调用。"""
    MCPState.document_ids = ids
    logger.info("[MCP] set_document_ids: %d 个文档", len(ids))
    return f"已设置 {len(ids)} 个可访问文档"


@mcp.tool()
async def search_documents(query: str, row_start: int | None = None, row_end: int | None = None) -> str:
    """从知识库中搜索与问题相关的文档内容。需要查找具体信息、数据、记录时调用。搜索词应具体，包含数据中可能的列名。
    如果要查询特定行号范围（如"第90到100行"、"第91行之后"），请传入 row_start 和 row_end 参数。"""
    logger.info("[MCP] search_documents: query='%s', row_start=%s, row_end=%s", query, row_start, row_end)

    ctx = SearchContext(document_ids=MCPState.document_ids)
    raw_result = rag_engine._execute_search(query, row_start, row_end, ctx)

    # 缓存状态，供后续工具使用
    MCPState.search_ctx = ctx

    # 解析结果，提取数据完整性信息
    rows_returned = len(ctx.last_search_chunks) if ctx.last_search_chunks else 0
    is_complete = "以上只显示了部分数据" not in raw_result if ctx.last_search_chunks else True
    available_actions = ["read_all_rows"] if not is_complete else []

    return json.dumps({
        "rows_returned": rows_returned,
        "is_complete": is_complete,
        "available_actions": available_actions,
        "data": raw_result,
    }, ensure_ascii=False)


@mcp.tool()
async def list_documents() -> str:
    """列出当前知识库中可检索的文档数量和名称。当用户问"有多少文件"、"能搜到几个文档"、"有哪些文档"等元信息问题时调用。"""
    logger.info("[MCP] list_documents")

    all_names = rag_engine.vector_store.get_document_names()
    if MCPState.document_ids:
        matched = {did: all_names[did] for did in MCPState.document_ids if did in all_names}
    else:
        matched = all_names

    if not matched:
        return "当前知识库中没有可检索的文档。"

    lines = [f"共 {len(matched)} 个文档："]
    for did, name in sorted(matched.items()):
        lines.append(f"- [{did}] {name}")
    return "\n".join(lines)


@mcp.tool()
async def calculate_sum(key_name: str, row_filter: str = "", content_filter: str = "") -> str:
    """对已检索到的文档内容中指定列（key）的数值进行精确求和。当用户问"总共"、"合计"、"一共多少钱"等加总问题时调用。必须先调用 search_documents 获取数据后才能使用此工具。
    content_filter: 可选，按内容过滤，格式为"列名=值"，如"品牌=万代"只对品牌为万代的行求和。"""
    logger.info("[MCP] calculate_sum: key_name='%s', row_filter='%s', content_filter='%s'", key_name, row_filter, content_filter)

    ctx = MCPState.search_ctx
    if not ctx:
        return "请先调用 search_documents 搜索数据。"

    return rag_engine._execute_sum(key_name, row_filter, content_filter, ctx)


@mcp.tool()
async def calculate_rank(key_name: str, ascending: bool, position: int = 1, content_filter: str = "") -> str:
    """从已检索到的文档内容中，对指定列（key）的数值排序并返回第N名的记录。当用户问"最贵"、"最便宜"、"第三高"等排名问题时调用。ascending=true=升序(最便宜/最低)，false=降序(最贵/最高)。必须先调用 search_documents 获取数据后才能使用此工具。
    content_filter: 可选，按内容过滤，格式为"列名=值"，如"品牌=万代"只对品牌为万代的记录排序。"""
    logger.info("[MCP] calculate_rank: key_name='%s', ascending=%s, position=%d", key_name, ascending, position)

    ctx = MCPState.search_ctx
    if not ctx:
        return "请先调用 search_documents 搜索数据。"

    return rag_engine._execute_rank(key_name, ascending, position, content_filter, ctx)


@mcp.tool()
async def read_all_rows() -> str:
    """读取当前搜索到的文档的全部数据行。当需要完整信息（如列出所有品牌、所有记录、完整清单）时调用。当前 search_documents 只返回部分数据，调用此工具可获取全文。必须先调用 search_documents 才能使用。"""
    logger.info("[MCP] read_all_rows")

    ctx = MCPState.search_ctx
    if not ctx:
        return "请先调用 search_documents 搜索数据。"

    return rag_engine._execute_read_all_rows(ctx)


async def main():
    """启动 MCP Server（stdio 模式）"""
    global rag_engine

    # 初始化向量数据库和 RAG 引擎
    vs = VectorStore()
    rag_engine = RAGEngine(vs)
    logger.info("[MCP Server] 初始化完成，等待连接...")

    await mcp.run_stdio_async()


if __name__ == "__main__":
    asyncio.run(main())

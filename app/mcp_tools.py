from langchain_core.tools import tool

from app.mcp_client import MCPClient


def create_mcp_tools(mcp_client: MCPClient):
    """创建 LangChain tools，内部通过 MCP Client 调用"""

    @tool
    async def search_documents(query: str, row_start: int = None, row_end: int = None) -> str:
        """从知识库中搜索与问题相关的文档内容。需要查找具体信息、数据、记录时调用。搜索词应具体，包含数据中可能的列名。
        如果要查询特定行号范围（如"第90到100行"、"第91行之后"），请传入 row_start 和 row_end 参数。"""
        return await mcp_client.call_tool("search_documents", {
            "query": query,
            "row_start": row_start,
            "row_end": row_end,
        })

    @tool
    async def list_documents() -> str:
        """列出当前知识库中可检索的文档数量和名称。当用户问"有多少文件"、"能搜到几个文档"、"有哪些文档"等元信息问题时调用。"""
        return await mcp_client.call_tool("list_documents", {})

    @tool
    async def calculate_sum(key_name: str, row_filter: str = "", content_filter: str = "") -> str:
        """对已检索到的文档内容中指定列（key）的数值进行精确求和。当用户问"总共"、"合计"、"一共多少钱"等加总问题时调用。必须先调用 search_documents 获取数据后才能使用此工具。
        content_filter: 可选，按内容过滤，格式为"列名=值"，如"品牌=万代"只对品牌为万代的行求和。"""
        return await mcp_client.call_tool("calculate_sum", {
            "key_name": key_name,
            "row_filter": row_filter,
            "content_filter": content_filter,
        })

    @tool
    async def calculate_rank(key_name: str, ascending: bool, position: int = 1, content_filter: str = "") -> str:
        """从已检索到的文档内容中，对指定列（key）的数值排序并返回第N名的记录。当用户问"最贵"、"最便宜"、"第三高"等排名问题时调用。ascending=true=升序(最便宜/最低)，false=降序(最贵/最高)。必须先调用 search_documents 获取数据后才能使用此工具。
        content_filter: 可选，按内容过滤，格式为"列名=值"，如"品牌=万代"只对品牌为万代的记录排序。"""
        return await mcp_client.call_tool("calculate_rank", {
            "key_name": key_name,
            "ascending": ascending,
            "position": position,
            "content_filter": content_filter,
        })

    @tool
    async def read_all_rows() -> str:
        """读取当前搜索到的文档的全部数据行。当需要完整信息（如列出所有品牌、所有记录、完整清单）时调用。当前 search_documents 只返回部分数据，调用此工具可获取全文。必须先调用 search_documents 才能使用。"""
        return await mcp_client.call_tool("read_all_rows", {})

    return [search_documents, list_documents, calculate_sum, calculate_rank, read_all_rows]

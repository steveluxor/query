import logging
import os
from contextlib import AsyncExitStack

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

logger = logging.getLogger(__name__)


class MCPClient:
    """MCP Client 封装：连接 MCP Server 并调用工具"""

    def __init__(self, server_command: str, server_args: list[str], env: dict[str, str] | None = None):
        # 继承当前进程的环境变量，确保 MCP Server 能访问配置
        process_env = os.environ.copy()
        if env:
            process_env.update(env)

        self.server_params = StdioServerParameters(
            command=server_command,
            args=server_args,
            env=process_env,
        )
        self.session: ClientSession | None = None
        self._exit_stack: AsyncExitStack | None = None

    async def connect(self):
        """建立连接"""
        logger.info("[MCP Client] 连接 Server: %s %s", self.server_params.command, self.server_params.args)

        self._exit_stack = AsyncExitStack()
        transport = await self._exit_stack.enter_async_context(
            stdio_client(self.server_params)
        )
        read, write = transport
        self.session = await self._exit_stack.enter_async_context(
            ClientSession(read, write)
        )
        await self.session.initialize()
        logger.info("[MCP Client] 连接成功")

    async def disconnect(self):
        """断开连接"""
        if self._exit_stack:
            await self._exit_stack.aclose()
            self._exit_stack = None
            self.session = None
            logger.info("[MCP Client] 已断开连接")

    async def call_tool(self, tool_name: str, arguments: dict) -> str:
        """调用工具"""
        if not self.session:
            raise RuntimeError("MCP Client 未连接")

        logger.info("[MCP Client] 调用工具: %s(%s)", tool_name, arguments)
        result = await self.session.call_tool(tool_name, arguments)
        return result.content[0].text

    async def list_tools(self) -> list:
        """获取工具列表"""
        if not self.session:
            raise RuntimeError("MCP Client 未连接")

        result = await self.session.list_tools()
        return result.tools

import asyncio
import time
import uuid
import logging

from app.models.mcp_session import MCPSession

logger = logging.getLogger(__name__)


class SessionManager:
    """管理 MCP 工具执行 session 的生命周期"""
    _TTL = 1800  # 30分钟自动过期

    def __init__(self, max_sessions: int = 500):
        self._sessions: dict[str, MCPSession] = {}
        self._lock = asyncio.Lock()
        self._max_sessions = max_sessions

    async def create(self, session_id: str = "") -> str:
        """创建新 session，返回 session_id"""
        if not session_id:
            session_id = str(uuid.uuid4())
        async with self._lock:
            self._sessions[session_id] = MCPSession(session_id=session_id)
        logger.info("[SessionManager] 创建 session: %s", session_id[:8])
        return session_id

    async def get(self, session_id: str) -> MCPSession:
        """获取 session，不存在则自动创建"""
        async with self._lock:
            self._evict()
            session = self._sessions.get(session_id)
            if not session:
                session = MCPSession(session_id=session_id)
                self._sessions[session_id] = session
            session.last_active = time.time()
            return session

    async def delete(self, session_id: str):
        """删除 session"""
        async with self._lock:
            self._sessions.pop(session_id, None)
        logger.info("[SessionManager] 删除 session: %s", session_id[:8])

    def _evict(self):
        """清理过期 session + LRU 上限淘汰（内部调用，已在锁内）"""
        now = time.time()
        # 1. TTL 过期淘汰
        expired = [k for k, v in self._sessions.items() if now - v.last_active > self._TTL]
        for k in expired:
            del self._sessions[k]
            logger.info("[SessionManager] 过期清理: %s", k[:8])

        # 2. LRU 上限淘汰
        over = len(self._sessions) - self._max_sessions
        if over > 0:
            sorted_items = sorted(self._sessions.items(), key=lambda x: x[1].last_active)
            for sid, _ in sorted_items[:over]:
                del self._sessions[sid]
            logger.info("[SessionManager] LRU 淘汰 %d 个 session", over)

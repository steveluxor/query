"""Redis 读取封装（只读，写入由 Java 负责）"""
import json
import logging

import redis.asyncio as aioredis

from app.config import settings
from app.core.agent_memory import AgentMemory

logger = logging.getLogger(__name__)


class RedisStore:
    """Redis 读取封装

    Python 只读 Redis 不写。写入由 Java 在收到 response 后完成。
    """

    def __init__(self):
        self.client = aioredis.Redis(
            host=settings.redis_host,
            port=settings.redis_port,
            password=settings.redis_password or None,
            db=settings.redis_db,
            decode_responses=True,
        )

    async def get_recent_history(self, session_id: str, limit: int = 10) -> list[dict]:
        """读取 Redis 中最近 N 轮对话"""
        key = f"{settings.redis_history_key_prefix}{session_id}"
        raw = await self.client.lrange(key, -limit, -1)
        return [json.loads(item) for item in raw] if raw else []

    async def get_memory(self, session_id: str) -> AgentMemory | None:
        """读取 Java 写入的 AgentMemory 快照"""
        key = f"{settings.redis_memory_key_prefix}{session_id}"
        raw = await self.client.get(key)
        if not raw:
            return None
        data = json.loads(raw)
        return AgentMemory.from_dict(data, session_id)

    async def safe_get_memory(self, session_id: str) -> AgentMemory | None:
        """Redis 不可用时返回 None，不抛异常"""
        try:
            return await self.get_memory(session_id)
        except Exception as e:
            logger.warning("Redis memory 读取失败（降级）: %s", e)
            return None

    async def safe_get_history(self, session_id: str, limit: int = 10) -> list[dict]:
        """Redis 不可用时返回空列表，不抛异常"""
        try:
            return await self.get_recent_history(session_id, limit)
        except Exception as e:
            logger.warning("Redis history 读取失败（降级）: %s", e)
            return []

    async def close(self):
        await self.client.close()

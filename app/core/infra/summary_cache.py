import json
import random
import time
import httpx

EXPIRE_MIN = 2 * 24 * 3600   # 2天
EXPIRE_MAX = 3 * 24 * 3600   # 3天
RENEW_TTL = 7 * 24 * 3600    # Redis 物理 TTL 7天（兜底）


class DocumentSummaryCache:
    """文档摘要缓存：Redis 逻辑过期 + Java API 回源

    Redis value: {"summary": "...", "expire_at": timestamp}
    无进程内状态，Python 后端保持无状态
    """

    def __init__(self, redis_client, java_base_url):
        self.redis = redis_client
        self.java_url = java_base_url

    def _make_key(self, document_id: int) -> str:
        return f"doc:summary:{document_id}"

    def _make_value(self, summary: str) -> str:
        expire_at = time.time() + random.uniform(EXPIRE_MIN, EXPIRE_MAX)
        return json.dumps({"summary": summary, "expire_at": expire_at})

    async def get(self, document_id: int) -> str | None:
        key = self._make_key(document_id)

        raw = await self.redis.get(key)
        if raw:
            try:
                data = json.loads(raw)
                if data["expire_at"] > time.time():
                    new_expire = time.time() + random.uniform(EXPIRE_MIN, EXPIRE_MAX)
                    data["expire_at"] = new_expire
                    await self.redis.set(key, json.dumps(data), ex=RENEW_TTL)
                    return data["summary"]
            except (json.JSONDecodeError, KeyError):
                pass

        summary = await self._fetch_from_java(document_id)
        if summary:
            await self.redis.set(key, self._make_value(summary), ex=RENEW_TTL)
        return summary

    async def set(self, document_id: int, summary: str):
        key = self._make_key(document_id)
        await self._save_to_java(document_id, summary)
        await self.redis.set(key, self._make_value(summary), ex=RENEW_TTL)

    async def delete(self, document_id: int):
        key = self._make_key(document_id)
        await self.redis.delete(key)

    async def get_batch(self, document_ids: list[int]) -> dict[int, str]:
        result = {}
        for did in document_ids:
            s = await self.get(did)
            if s:
                result[did] = s
        return result

    async def _fetch_from_java(self, document_id: int) -> str | None:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(f"{self.java_url}/document/{document_id}/summary")
            if resp.status_code == 200:
                data = resp.json()
                return data.get("data")
        return None

    async def _save_to_java(self, document_id: int, summary: str):
        async with httpx.AsyncClient(timeout=10) as client:
            await client.put(
                f"{self.java_url}/document/{document_id}/summary",
                json={"summary": summary}
            )

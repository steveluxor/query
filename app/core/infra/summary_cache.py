import httpx


class DocumentSummaryCache:
    """Java 管理的文档摘要缓存客户端。

    Python 不读取或写入 ``doc:summary:*`` Redis 键。Java 负责缓存读取、
    逻辑过期、并发刷新、回源和失效；Python 仅消费批量摘要结果。
    """

    def __init__(self, java_base_url: str, internal_service_token: str):
        self.java_url = java_base_url.rstrip("/")
        self.headers = {"X-Internal-Service-Token": internal_service_token}

    async def get_batch(self, document_ids: list[int]) -> dict[int, str]:
        ids = list(dict.fromkeys(document_id for document_id in document_ids if document_id > 0))
        if not ids:
            return {}

        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.post(
                f"{self.java_url}/document/internal/summaries/query",
                headers=self.headers,
                json={"documentIds": ids},
            )
            response.raise_for_status()
            payload = response.json()

        if payload.get("code") != 200:
            raise RuntimeError(payload.get("message", "Java 文档摘要查询失败"))

        data = payload.get("data") or {}
        return {
            int(document_id): summary
            for document_id, summary in data.items()
            if isinstance(summary, str) and summary.strip()
        }

    async def set(self, document_id: int, summary: str):
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.put(
                f"{self.java_url}/document/internal/summaries/{document_id}",
                headers=self.headers,
                json={"summary": summary},
            )
            response.raise_for_status()
            payload = response.json()

        if payload.get("code") != 200:
            raise RuntimeError(payload.get("message", "Java 文档摘要保存失败"))

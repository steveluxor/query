import pytest

from app.core.infra.summary_cache import DocumentSummaryCache


class FakeResponse:
    def __init__(self, payload, status_code=200):
        self.payload = payload
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        return self.payload


class FakeAsyncClient:
    def __init__(self, response):
        self.response = response
        self.calls = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        return False

    async def post(self, url, **kwargs):
        self.calls.append(("post", url, kwargs))
        return self.response

    async def put(self, url, **kwargs):
        self.calls.append(("put", url, kwargs))
        return self.response


@pytest.mark.anyio
async def test_get_batch_uses_one_authenticated_java_request(monkeypatch):
    client = FakeAsyncClient(FakeResponse({"code": 200, "data": {"7": "摘要A", "9": "摘要B"}}))
    monkeypatch.setattr("app.core.infra.summary_cache.httpx.AsyncClient", lambda **kwargs: client)
    cache = DocumentSummaryCache("http://java:8085/", "internal-token")

    summaries = await cache.get_batch([7, 9, 7, -1])

    assert summaries == {7: "摘要A", 9: "摘要B"}
    assert client.calls == [(
        "post",
        "http://java:8085/document/internal/summaries/query",
        {
            "headers": {"X-Internal-Service-Token": "internal-token"},
            "json": {"documentIds": [7, 9]},
        },
    )]


@pytest.mark.anyio
async def test_set_delegates_summary_write_to_java(monkeypatch):
    client = FakeAsyncClient(FakeResponse({"code": 200, "data": None}))
    monkeypatch.setattr("app.core.infra.summary_cache.httpx.AsyncClient", lambda **kwargs: client)
    cache = DocumentSummaryCache("http://java:8085", "internal-token")

    await cache.set(7, "新的摘要")

    assert client.calls == [(
        "put",
        "http://java:8085/document/internal/summaries/7",
        {
            "headers": {"X-Internal-Service-Token": "internal-token"},
            "json": {"summary": "新的摘要"},
        },
    )]

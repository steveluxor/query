from functools import lru_cache

import httpx
from langchain_openai import ChatOpenAI

from app.config import settings


@lru_cache(maxsize=8)
def create_llm(temperature: float = 0.1, max_tokens: int = 4096, timeout: int = 120) -> ChatOpenAI:
    """统一创建 LLM 客户端，相同参数返回缓存实例"""
    async_client = httpx.AsyncClient(
        limits=httpx.Limits(max_connections=20, max_keepalive_connections=10)
    )
    return ChatOpenAI(
        api_key=settings.llm_api_key,
        base_url=settings.llm_base_url,
        model=settings.llm_model_name,
        temperature=temperature,
        max_tokens=max_tokens,
        timeout=timeout,
        http_async_client=async_client,
    )

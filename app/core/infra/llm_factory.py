from langchain_openai import ChatOpenAI

from app.config import settings


def create_llm(temperature: float = 0.1, max_tokens: int = 4096, timeout: int = 30) -> ChatOpenAI:
    """统一创建 LLM 客户端，消除重复配置"""
    return ChatOpenAI(
        api_key=settings.llm_api_key,
        base_url=settings.llm_base_url,
        model=settings.llm_model_name,
        temperature=temperature,
        max_tokens=max_tokens,
        timeout=timeout,
    )

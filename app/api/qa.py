import concurrent.futures

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel

from app.core.rag_engine import RAGEngine
from app.core.agent_memory import AgentMemory
from app.core.redis_store import RedisStore
from app.models.schemas import QuestionRequest, AnswerResponse


def get_rag_engine(request: Request) -> RAGEngine:
    return request.app.state.rag_engine


def get_agent_memory(request: Request) -> AgentMemory:
    return request.app.state.agent_memory


def get_redis_store(request: Request) -> RedisStore:
    return request.app.state.redis_store


router = APIRouter(prefix="/qa", tags=["Q&A"])


@router.post("/ask", response_model=AnswerResponse)
async def ask_question(
    request: QuestionRequest,
    engine: RAGEngine = Depends(get_rag_engine),
    agent_memory: AgentMemory = Depends(get_agent_memory),
    redis_store: RedisStore = Depends(get_redis_store),
):
    """接收用户问题，执行 RAG 检索 + LLM 生成"""
    memory_context = None
    effective_history = request.history or []

    if request.session_id:
        # 1. 从 Redis 恢复已固化的 AgentMemory
        if request.session_id not in agent_memory._sessions:
            loaded = await redis_store.safe_get_memory(request.session_id)
            if loaded:
                agent_memory._sessions[request.session_id] = loaded._sessions[request.session_id]

        # 2. 从 Redis 读取最近对话历史（Java 写入的 qa:history:{id}）
        redis_history = await redis_store.safe_get_history(request.session_id)
        if redis_history:
            effective_history = redis_history

        # 3. Redis 无记忆 + Java 传了全量历史 → 重建
        if request.session_id not in agent_memory._sessions and request.history:
            agent_memory.rebuild_from_history(
                request.session_id, request.history,
                preferences=request.preferences,
            )

        # 4. 格式化记忆上下文
        memory_context = agent_memory.format_context(request.session_id)

    # 执行引擎 + 偏好检测并行
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
        pref_future = executor.submit(
            agent_memory.update_preferences, request.session_id, request.question,
        ) if request.session_id else None

        result = engine.answer(
            question=request.question,
            top_k=request.top_k,
            document_ids=request.document_ids,
            history=effective_history,
            strategy=request.strategy,
            memory_context=memory_context,
        )

        if pref_future:
            pref_future.result()

    # 更新记忆（事实/里程碑）
    if request.session_id:
        source_docs = result.get("sources", [])
        doc_names = list(dict.fromkeys(
            s.get("file_name", "") for s in source_docs if s.get("file_name")
        ))
        agent_memory.update(
            request.session_id,
            {
                "question": request.question,
                "answer": result.get("answer", ""),
                "is_agg": result.get("is_agg", False),
                "tools_called": result.get("tools_called", []),
                "document_ids": request.document_ids,
                "document_names": doc_names,
            },
        )

    # 返回 memory_data（Java 会将它写入 Redis）
    memory_data = None
    if request.session_id:
        memory_data = agent_memory.to_dict(request.session_id)

    return AnswerResponse(
        answer=result["answer"],
        sources=result["sources"],
        is_agg=result.get("is_agg", False),
        tools_called=result.get("tools_called", []),
        session_id=request.session_id,
        memory_data=memory_data,
    )

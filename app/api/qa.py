from fastapi import APIRouter, Depends, Request

from app.core.rag_engine import RAGEngine
from app.models.schemas import QuestionRequest, AnswerResponse


def get_rag_engine(request: Request) -> RAGEngine:
    return request.app.state.rag_engine


router = APIRouter(prefix="/qa", tags=["Q&A"])


@router.post("/ask", response_model=AnswerResponse)
async def ask_question(
    request: QuestionRequest,
    engine: RAGEngine = Depends(get_rag_engine),
):
    """接收用户问题，执行 RAG 检索 + LLM 生成"""
    result = engine.answer(
        question=request.question,
        top_k=request.top_k,
        document_ids=request.document_ids,
        history=request.history,
        strategy=request.strategy,
    )
    return AnswerResponse(
        answer=result["answer"],
        sources=result["sources"],
        is_agg=result.get("is_agg", False),
    )
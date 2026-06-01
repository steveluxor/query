from fastapi import APIRouter, Depends, Request

from app.core.rag_engine import RAGEngine
from app.core.vector_store import VectorStore
from app.models.schemas import QuestionRequest, AnswerResponse


def get_vector_store(request: Request) -> VectorStore:
    return request.app.state.vector_store


router = APIRouter(prefix="/qa", tags=["Q&A"])


@router.post("/ask", response_model=AnswerResponse)
async def ask_question(
    request: QuestionRequest,
    vector_store: VectorStore = Depends(get_vector_store),
):
    """接收用户问题，执行 RAG 检索 + LLM 生成"""
    engine = RAGEngine(vector_store)
    result = engine.answer(
        question=request.question,
        top_k=request.top_k,
        document_ids=request.document_ids,
        history=request.history,
    )
    return AnswerResponse(
        answer=result["answer"],
        sources=result["sources"],
    )

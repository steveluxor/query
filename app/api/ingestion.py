from fastapi import APIRouter, Depends, HTTPException, Request

from app.core.document_processor import DocumentProcessor
from app.core.vector_store import VectorStore
from app.models.schemas import IngestRequest, IngestResponse


def get_vector_store(request: Request) -> VectorStore:
    return request.app.state.vector_store


router = APIRouter(prefix="/ingest", tags=["Ingestion"])

processor = DocumentProcessor()


@router.post("/document", response_model=IngestResponse)
async def ingest_document(
    request: IngestRequest,
    vector_store: VectorStore = Depends(get_vector_store),
):
    """接收文档，执行解析、切片、向量化并存入向量库"""
    try:
        # 清理该文档的旧 chunk，防止残留数据干扰
        vector_store.delete_by_document_id(request.document_id)
        chunks = processor.process(
            file_path=request.file_path,
            document_id=request.document_id,
            file_name=request.file_name,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="文件未找到")

    texts = [c["text"] for c in chunks]
    metadatas = [c["metadata"] for c in chunks]

    vector_store.add_texts(texts, metadatas)

    return IngestResponse(
        document_id=request.document_id,
        status="success",
    )


@router.delete("/document/{document_id}")
async def delete_document(
    document_id: int,
    vector_store: VectorStore = Depends(get_vector_store),
):
    """从向量库中删除指定文档的所有切片"""
    vector_store.delete_by_document_id(document_id)
    return {"status": "deleted", "document_id": document_id}

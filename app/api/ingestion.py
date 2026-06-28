import os
import tempfile

import minio
from fastapi import APIRouter, Depends, HTTPException, Request

from app.config import settings
from app.core.document_processor import DocumentProcessor
from app.core.vector_store import VectorStore
from app.models.schemas import IngestRequest, IngestResponse


def get_vector_store(request: Request) -> VectorStore:
    return request.app.state.vector_store


router = APIRouter(prefix="/ingest", tags=["Ingestion"])

processor = DocumentProcessor()

_minio_client: minio.Minio | None = None


def _get_minio_client() -> minio.Minio:
    global _minio_client
    if _minio_client is None:
        _minio_client = minio.Minio(
            settings.minio_endpoint.replace("http://", ""),
            access_key=settings.minio_access_key,
            secret_key=settings.minio_secret_key,
            secure=False,
        )
    return _minio_client


@router.post("/document", response_model=IngestResponse)
async def ingest_document(
    request: IngestRequest,
    vector_store: VectorStore = Depends(get_vector_store),
):
    """接收文档，执行解析、切片、向量化并存入向量库"""
    file_path = request.file_path
    need_cleanup = False

    try:
        # 如果文件不存在（跨容器调用），从 MinIO 下载
        if not os.path.exists(file_path):
            suffix = os.path.splitext(request.file_name)[1] if "." in request.file_name else ""
            file_path = os.path.join(tempfile.gettempdir(), f"rag_{request.document_id}{suffix}")
            _get_minio_client().fget_object(settings.minio_bucket, request.file_path, file_path)
            need_cleanup = True
        # 共享目录中的临时文件也需要清理
        elif file_path.startswith("/tmp/"):
            need_cleanup = True

        chunks = processor.process(
            file_path=file_path,
            document_id=request.document_id,
            file_name=request.file_name,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="文件未找到")
    finally:
        # 清理临时文件（共享目录或本地下载的）
        if need_cleanup and os.path.exists(file_path):
            os.unlink(file_path)

    texts = [c["text"] for c in chunks]
    metadatas = [c["metadata"] for c in chunks]

    # 处理成功后清理旧向量，防止残留数据干扰
    vector_store.delete_by_document_id(request.document_id)

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

from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

from app.api import ingestion, qa
from app.core.vector_store import VectorStore


@asynccontextmanager
async def lifespan(application: FastAPI):
    # 全局初始化：向量存储（首次运行会下载 embedding 模型）
    vs = VectorStore()
    application.state.vector_store = vs
    yield
    # 资源清理
    application.state.vector_store = None


app = FastAPI(
    title="AI RAG Service",
    description="企业智能文档问答系统 - AI 微服务",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(ingestion.router)
app.include_router(qa.router)


@app.get("/health")
async def health():
    return {"status": "ok"}

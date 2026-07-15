import logging

from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.exceptions import RequestValidationError

from app.api import ingestion, qa
from app.core.rag_engine import RAGEngine
from app.core.vector_store import VectorStore
from app.core.agent_memory import AgentMemory
from app.core.redis_store import RedisStore
from app.core.agent_orchestrator import AgentOrchestrator
from app.mcp_client import MCPClient
from app.exceptions import BizException, ErrorCode

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")


@asynccontextmanager
async def lifespan(application: FastAPI):
    # 全局初始化
    vs = VectorStore()
    application.state.vector_store = vs
    application.state.rag_engine = RAGEngine(vs)
    application.state.agent_memory = AgentMemory()
    application.state.redis_store = RedisStore()

    # 初始化 MCP Client
    mcp_client = MCPClient(
        server_command="python",
        server_args=["-m", "app.mcp_server"]
    )
    await mcp_client.connect()
    application.state.mcp_client = mcp_client

    application.state.orchestrator = AgentOrchestrator(
        rag_engine=application.state.rag_engine,
        agent_memory=application.state.agent_memory,
        redis_store=application.state.redis_store,
        mcp_client=mcp_client,
    )
    yield
    # 资源清理
    await mcp_client.disconnect()
    application.state.rag_engine = None
    application.state.agent_memory = None
    await application.state.redis_store.close()
    vs.close()


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


@app.exception_handler(BizException)
async def biz_exception_handler(request: Request, exc: BizException):
    """业务异常处理"""
    return JSONResponse(
        status_code=400,
        content={"code": exc.code, "message": exc.message}
    )


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    """参数校验异常处理"""
    errors = []
    for error in exc.errors():
        field = ".".join(str(loc) for loc in error["loc"])
        errors.append(f"{field}: {error['msg']}")
    return JSONResponse(
        status_code=422,
        content={"code": ErrorCode.PARAM_ERROR, "message": "参数校验失败", "details": errors}
    )


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    """全局异常兜底处理"""
    logging.getLogger(__name__).error(f"服务器错误: {exc}", exc_info=True)
    return JSONResponse(
        status_code=500,
        content={"code": ErrorCode.SERVER_ERROR, "message": "服务器内部错误"}
    )


@app.get("/health")
async def health():
    return {"status": "ok"}

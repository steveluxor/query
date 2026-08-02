import asyncio
import json
import logging
import os
from uuid import uuid4

import httpx
from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse

from app.core.agent_memory import AgentMemory
from app.core.agent_orchestrator import AgentOrchestrator
from app.core.agent_context import AgentContext
from app.core.runtime_event_bus import RuntimeEventBus, EventType
from app.models.data_types import CodeResult
from app.models.schemas import QuestionRequest, MultiAgentResponse, Source, AgentStepInfo

logger = logging.getLogger(__name__)


def get_agent_memory(request: Request) -> AgentMemory:
    return request.app.state.agent_memory


def get_orchestrator(request: Request) -> AgentOrchestrator:
    return request.app.state.orchestrator


def get_event_bus(request: Request) -> RuntimeEventBus:
    return request.app.state.event_bus


router = APIRouter(prefix="/qa", tags=["Q&A"])

# per-run 活跃任务管理（无状态，仅运行期跟踪）
_active_tasks: dict[str, asyncio.Task] = {}


@router.post("/ask")
async def ask_question(
    request: QuestionRequest,
    orchestrator: AgentOrchestrator = Depends(get_orchestrator),
    agent_memory: AgentMemory = Depends(get_agent_memory),
    event_bus: RuntimeEventBus = Depends(get_event_bus),
):
    """异步启动 Multi-Agent 执行，立即返回 run_id 供 SSE 连接"""
    run_id = str(uuid4())

    context = AgentContext(
        question=request.question,
        session_id=request.session_id,
        document_ids=request.document_ids,
        history=request.history,
        preferences=request.preferences,
        run_id=run_id,
        event_bus=event_bus,
    )

    # 预创建 EventBus channel（避免事件丢失：task 启动前 channel 已就绪）
    event_bus.create_channel(run_id)

    # 后台异步执行
    task = asyncio.create_task(_run_and_emit(context, orchestrator, agent_memory, event_bus))
    _active_tasks[run_id] = task
    task.add_done_callback(lambda t: _active_tasks.pop(run_id, None))

    return {"run_id": run_id, "session_id": context.session_id}


@router.get("/runtime/{run_id}")
async def stream_runtime(run_id: str, event_bus: RuntimeEventBus = Depends(get_event_bus)):
    """Runtime Event Stream — Workflow 层状态变化（低频）"""
    queue = event_bus.subscribe(run_id)

    async def generate():
        try:
            while True:
                event = await queue.get()
                event_type = event.type.value
                payload = json.dumps({"type": event_type, "data": event.data})
                yield f"event: {event_type}\ndata: {payload}\n\n"
                if event.type in (EventType.RUNTIME_COMPLETED, EventType.RUNTIME_ERROR):
                    break
        except asyncio.CancelledError:
            pass
        finally:
            event_bus.cleanup(run_id)

    return StreamingResponse(generate(), media_type="text/event-stream")


@router.get("/answer/{run_id}")
async def stream_answer(run_id: str, event_bus: RuntimeEventBus = Depends(get_event_bus)):
    """Answer Stream — Generation 层 token 增量（高频）"""
    queue = event_bus.subscribe(run_id)

    async def generate():
        try:
            while True:
                event = await queue.get()
                if event.type == EventType.TOKEN_CHUNK:
                    yield f"data: {json.dumps(event.data)}\n\n"
                elif event.type in (EventType.RUNTIME_COMPLETED, EventType.RUNTIME_ERROR):
                    yield f"data: {json.dumps({'type': 'done'})}\n\n"
                    break
        except asyncio.CancelledError:
            pass

    return StreamingResponse(generate(), media_type="text/event-stream")


async def _run_and_emit(
    context: AgentContext,
    orchestrator: AgentOrchestrator,
    agent_memory: AgentMemory,
    event_bus: RuntimeEventBus,
):
    """后台执行 Orchestrator + callback 持久化 + 发射完成事件"""
    try:
        context = await orchestrator.run(context)

        # 构建完整结果（question 需要在 callback 中用于 DB 持久化）
        result = _build_response(context, agent_memory)
        result["question"] = context.question

        # Phase 2: callback Java 持久化（成功后再 emit completed）
        await _callback_java(context.session_id, result)

        # 持久化成功后发射完成事件
        await context.emit(EventType.RUNTIME_COMPLETED, result)

    except Exception as e:
        logger.error("[QA] 执行失败: %s", e, exc_info=True)
        await context.emit(EventType.RUNTIME_ERROR, {"error": str(e)})
    finally:
        # 清理 MCP session
        if context.mcp_session_id:
            try:
                await orchestrator.mcp_client.cleanup_session(context.mcp_session_id)
            except Exception:
                pass


def _build_response(context: AgentContext, agent_memory: AgentMemory) -> dict:
    """从 context 构建 MultiAgentResponse dict（与原 ask_question 返回结构一致）"""
    plan = None
    if context.plan and context.plan.tasks:
        plan = [
            {
                "id": t.id,
                "agent": t.agent,
                "objective": t.objective,
                "depends_on": t.depends_on,
                "status": t.status.value,
                "duration_ms": t.duration_ms,
                "summary": t.summary,
                "tools_used": t.tools_used,
                "artifacts": t.artifacts,
            }
            for t in context.plan.tasks
        ]

    memory_data = None
    if context.session_id:
        memory_data = agent_memory.to_dict(context.session_id)

    image_urls = []
    generated_code = ""
    code_stdout = ""
    code_error = ""
    code_success = True
    code_result = context.get_output("code_result")
    if isinstance(code_result, CodeResult):
        if code_result.image_data:
            image_urls = code_result.image_data
        generated_code = code_result.code or ""
        code_stdout = code_result.stdout or ""
        code_error = code_result.error or ""
        code_success = code_result.success

    return {
        "answer": context.get_output("answer") or "",
        "sources": context.get_output("sources") or [],
        "is_agg": context.is_agg,
        "tools_called": context.tools_called,
        "session_id": context.session_id,
        "memory_data": memory_data,
        "plan": plan,
        "agent_trace": [
            {"name": s.name, "duration_ms": s.duration_ms, "summary": s.summary}
            for s in context.steps
        ],
        "image_urls": image_urls,
        "generated_code": generated_code,
        "code_stdout": code_stdout,
        "code_error": code_error,
        "code_success": code_success,
    }


async def _callback_java(session_id: str | None, result: dict):
    """POST 完整结果到 Java /qa/callback 端点，由 Java 持久化到 MySQL/Redis/MinIO"""
    if not session_id:
        return

    java_base_url = os.getenv("JAVA_BASE_URL", "http://localhost:8085")
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(f"{java_base_url}/qa/callback", json={
                "session_id": session_id,
                "result": result,
            })
            if resp.status_code == 200:
                logger.info("[QA] callback 持久化成功: session_id=%s", session_id)
            else:
                logger.warning("[QA] callback 返回异常: status=%d, body=%s", resp.status_code, resp.text)
    except Exception as e:
        logger.error("[QA] callback 失败: %s", e)

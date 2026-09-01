import asyncio
import json
import logging
import os
from uuid import uuid4

import httpx
from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import StreamingResponse

from app.config import settings
from app.core.agent_memory import AgentMemory
from app.core.agent_orchestrator import AgentOrchestrator
from app.core.agent_context import AgentContext
from app.core.infra.redis_store import RedisStore
from app.core.runtime_event_bus import RuntimeEvent, RuntimeEventBus, EventType
from app.models.data_types import CodeResult
from app.models.schemas import QuestionRequest, StopRequest

logger = logging.getLogger(__name__)


def get_agent_memory(request: Request) -> AgentMemory:
    return request.app.state.agent_memory


def get_orchestrator(request: Request) -> AgentOrchestrator:
    return request.app.state.orchestrator


def get_event_bus(request: Request) -> RuntimeEventBus:
    return request.app.state.event_bus


def get_redis_store(request: Request) -> RedisStore:
    return request.app.state.redis_store


router = APIRouter(prefix="/qa", tags=["Q&A"])

# per-run 活跃任务管理（无状态，仅运行期跟踪）
_active_tasks: dict[str, asyncio.Task] = {}


def _track_active_task(run_id: str, task: asyncio.Task) -> None:
    _active_tasks[run_id] = task
    task.add_done_callback(lambda _: _active_tasks.pop(run_id, None))


@router.post("/ask")
async def ask_question(
    request: QuestionRequest,
    orchestrator: AgentOrchestrator = Depends(get_orchestrator),
    agent_memory: AgentMemory = Depends(get_agent_memory),
    event_bus: RuntimeEventBus = Depends(get_event_bus),
    redis_store: RedisStore = Depends(get_redis_store),
):
    """异步启动 Multi-Agent 执行，立即返回 run_id 供 SSE 连接"""
    run_id = str(uuid4())

    context = AgentContext(
        question=request.question,
        user_id=request.user_id,
        session_id=request.session_id,
        document_ids=request.document_ids,
        document_versions=request.document_versions,
        history=request.history,
        preferences=request.preferences,
        run_id=run_id,
        event_bus=event_bus,
        run_state_store=redis_store.run_state,
    )

    # 预创建 EventBus channel（避免事件丢失：task 启动前 channel 已就绪）
    event_bus.create_channel(run_id)
    await redis_store.run_state.create_run(context)
    await redis_store.run_state.renew_lease(run_id, orchestrator.worker_id)

    # 后台异步执行
    task = asyncio.create_task(_run_and_emit(context, orchestrator, agent_memory, event_bus))
    _track_active_task(run_id, task)

    return {"run_id": run_id, "session_id": context.session_id}


@router.get("/runtime/{run_id}")
async def stream_runtime(
    run_id: str,
    user_id: int,
    last_event_id: str | None = Header(None, alias="Last-Event-ID"),
    redis_store: RedisStore = Depends(get_redis_store),
):
    """Runtime Event Stream — Workflow 层状态变化（低频）"""
    meta = await redis_store.run_state.get_run_meta(run_id)
    if not meta:
        raise HTTPException(status_code=404, detail="run not found")
    if meta.get("user_id") != str(user_id):
        raise HTTPException(status_code=403, detail="run does not belong to the current user")

    async def persisted_generate():
        event_id = last_event_id or "0-0"
        try:
            while True:
                events = await redis_store.run_state.read_events(run_id, event_id)
                if not events:
                    await asyncio.sleep(1)
                    yield ": ping\n\n"
                    continue
                for event_id, event_type, data in events:
                    payload = json.dumps({"type": event_type, "data": data})
                    yield f"id: {event_id}\nevent: {event_type}\ndata: {payload}\n\n"
                    if event_type in {
                        EventType.RUNTIME_COMPLETED.value,
                        EventType.RUNTIME_ERROR.value,
                        EventType.RUNTIME_CANCELLED.value,
                    }:
                        return
        except asyncio.CancelledError:
            return

    return StreamingResponse(persisted_generate(), media_type="text/event-stream")


@router.get("/active-runtime")
async def get_active_runtime(
    session_id: str,
    user_id: int,
    redis_store: RedisStore = Depends(get_redis_store),
):
    """Return the newest unfinished run in a conversation, if one exists."""
    meta = await redis_store.run_state.find_active_run(user_id=user_id, session_id=session_id)
    if not meta:
        return {"run_id": None}
    return {
        "run_id": meta["run_id"],
        "status": meta["status"],
        "question": meta.get("question", ""),
    }


@router.post("/stop")
async def stop_qa(
    req: StopRequest,
    event_bus: RuntimeEventBus = Depends(get_event_bus),
    redis_store: RedisStore = Depends(get_redis_store),
):
    """用户手动停止正在进行的问答：先发取消事件（SSE 流结束），再取消后台任务"""
    meta = await redis_store.run_state.get_run_meta(req.run_id)
    if not meta:
        raise HTTPException(status_code=404, detail="run not found")
    if meta.get("user_id") != str(req.user_id):
        raise HTTPException(status_code=403, detail="run does not belong to the current user")
    task = _active_tasks.get(req.run_id)
    if not task or task.done():
        logger.info("[QA] stop: run_id=%s 已不活跃", req.run_id)
        return {"ok": False, "reason": "run not active"}

    # 先发 RUNTIME_CANCELLED：所有 SSE 订阅者收到后 break，流自然结束
    payload = {"reason": "user_stopped"}
    await redis_store.run_state.append_event(req.run_id, EventType.RUNTIME_CANCELLED.value, payload)
    await redis_store.run_state.set_status(req.run_id, "cancelled")
    await event_bus.publish(RuntimeEvent(req.run_id, EventType.RUNTIME_CANCELLED, payload))
    # 再取消后台 DAG 任务（取消后不 emit completed/error，半成品不持久化）
    task.cancel()
    logger.info("[QA] stop: run_id=%s 已取消", req.run_id)
    return {"ok": True}


async def _run_and_emit(
    context: AgentContext,
    orchestrator: AgentOrchestrator,
    agent_memory: AgentMemory,
    event_bus: RuntimeEventBus,
):
    """后台执行 Orchestrator + callback 持久化 + 发射完成事件"""
    try:
        context = await orchestrator.run(context)
        await _finalize_run(context, agent_memory)

    except Exception as e:
        logger.error("[QA] 执行失败: %s", e, exc_info=True)
        if context.run_state_store:
            await context.run_state_store.set_status(context.run_id, "failed", error=str(e))
        await context.emit(EventType.RUNTIME_ERROR, {"error": str(e)})


async def _resume_and_emit(
    context: AgentContext,
    orchestrator: AgentOrchestrator,
    agent_memory: AgentMemory,
):
    """Resume an interrupted run using its existing run_id and checkpoint."""
    try:
        context = await (orchestrator.resume(context) if context.plan else orchestrator.run(context))
        await _finalize_run(context, agent_memory)
    except Exception as e:
        logger.error("[QA] 恢复执行失败: %s", e, exc_info=True)
        if context.run_state_store:
            await context.run_state_store.set_status(context.run_id, "failed", error=str(e))
        await context.emit(EventType.RUNTIME_ERROR, {"error": str(e), "recovered": True})


async def _finalize_run(context: AgentContext, agent_memory: AgentMemory) -> None:
    """Persist the final response only after all DAG nodes have completed."""
    result = _build_response(context, agent_memory)
    result["question"] = context.question
    await context.run_state_store.set_status(context.run_id, "finalizing")
    callback_ok = await _callback_java(context.session_id, result, agent_memory)
    if not callback_ok:
        raise RuntimeError("callback 持久化失败")
    await context.run_state_store.set_status(context.run_id, "completed")
    await context.emit(EventType.RUNTIME_COMPLETED, {"session_id": context.session_id})


async def recover_interrupted_runs(app) -> None:
    """Claim runs whose previous worker lease expired and resume them once."""
    redis_store: RedisStore = app.state.redis_store
    orchestrator: AgentOrchestrator = app.state.orchestrator
    event_bus: RuntimeEventBus = app.state.event_bus
    agent_memory: AgentMemory = app.state.agent_memory

    for run_id in await redis_store.run_state.list_expired_leases():
        if run_id in _active_tasks:
            continue
        if not await redis_store.run_state.claim_recovery(run_id, orchestrator.worker_id):
            continue
        context = await redis_store.run_state.load_context(run_id, event_bus=event_bus)
        if not context:
            logger.warning("[QA] 恢复 run %s 时找不到 checkpoint", run_id)
            continue
        context.run_state_store = redis_store.run_state
        event_bus.create_channel(run_id)
        logger.info("[QA] 恢复失联 run: %s", run_id)
        _track_active_task(run_id, asyncio.create_task(_resume_and_emit(context, orchestrator, agent_memory)))


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


async def _callback_java(session_id: str | None, result: dict, agent_memory: AgentMemory) -> bool:
    """POST 完整结果到 Java /qa/callback 端点，由 Java 持久化到 MySQL/Redis/MinIO。

    返回是否持久化成功：
    - 无 session_id → 无需持久化，视为成功
    - 成功（200）→ 清除 memory dirty 标记，返回 True
    - 异常或非 200 → 返回 False，调用方应发 RUNTIME_ERROR 而非 RUNTIME_COMPLETED
    """
    if not session_id:
        return True

    headers = {}
    if settings.callback_token:
        headers["X-Callback-Token"] = settings.callback_token

    java_base_url = os.getenv("JAVA_BASE_URL", settings.java_base_url)
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                f"{java_base_url}/qa/callback",
                json={"session_id": session_id, "result": result},
                headers=headers,
            )
            if resp.status_code == 200 and resp.json().get("code") == 200:
                logger.info("[QA] callback 持久化成功: session_id=%s", session_id)
                agent_memory.mark_synced(session_id)
                return True
            else:
                logger.warning("[QA] callback 返回异常: status=%d, body=%s", resp.status_code, resp.text)
                return False
    except Exception as e:
        logger.error("[QA] callback 失败: %s", e)
        return False

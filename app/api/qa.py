from fastapi import APIRouter, Depends, Request

from app.core.agent_memory import AgentMemory
from app.core.agent_orchestrator import AgentOrchestrator
from app.core.agent_context import AgentContext
from app.models.schemas import QuestionRequest, MultiAgentResponse, Source, AgentStepInfo


def get_agent_memory(request: Request) -> AgentMemory:
    return request.app.state.agent_memory


def get_orchestrator(request: Request) -> AgentOrchestrator:
    return request.app.state.orchestrator


router = APIRouter(prefix="/qa", tags=["Q&A"])


@router.post("/ask", response_model=MultiAgentResponse)
async def ask_question(
    request: QuestionRequest,
    orchestrator: AgentOrchestrator = Depends(get_orchestrator),
    agent_memory: AgentMemory = Depends(get_agent_memory),
):
    """接收用户问题，执行 Multi-Agent 协作问答"""
    context = AgentContext(
        question=request.question,
        session_id=request.session_id,
        document_ids=request.document_ids,
        history=request.history,
        top_k=request.top_k,
        preferences=request.preferences,
    )

    context = await orchestrator.run(context)

    # plan 序列化：TaskGraph → list[dict]
    plan = None
    if context.plan and context.plan.tasks:
        plan = [
            {"id": t.id, "agent": t.agent, "objective": t.objective, "depends_on": t.depends_on}
            for t in context.plan.tasks
        ]

    # 返回 memory_data（Java 会将它写入 Redis）
    memory_data = None
    if context.session_id:
        memory_data = agent_memory.to_dict(context.session_id)

    return MultiAgentResponse(
        answer=context.get_output("answer") or "",
        sources=[Source(**s) for s in (context.get_output("sources") or [])],
        is_agg=context.is_agg,
        tools_called=context.tools_called,
        session_id=context.session_id,
        memory_data=memory_data,
        plan=plan,
        agent_trace=[
            AgentStepInfo(name=s.name, duration_ms=s.duration_ms, summary=s.summary)
            for s in context.steps
        ],
    )

import time
from dataclasses import dataclass, field


@dataclass
class AgentStep:
    """单个 Agent 执行记录"""
    name: str
    duration_ms: int
    summary: str


@dataclass
class AgentContext:
    """Agent 间共享上下文"""
    question: str
    session_id: str | None = None
    document_ids: list[int] | None = None
    history: list[dict] | None = None
    memory_context: str | None = None
    top_k: int = 5
    preferences: dict | None = None

    # Knowledge Agent 输出
    knowledge_chunks: list = field(default_factory=list)
    knowledge_filtered: list = field(default_factory=list)
    knowledge_all_chunks: list = field(default_factory=list)

    # RAG / Analysis Agent 输出
    answer: str = ""
    sources: list[dict] = field(default_factory=list)
    tools_called: list[str] = field(default_factory=list)
    is_agg: bool = False
    plan: list[dict] | None = None

    # Critic Agent 填充
    critique: str = ""
    reflection_count: int = 0

    # 执行轨迹
    steps: list[AgentStep] = field(default_factory=list)
    start_time: float = field(default_factory=time.time)

from __future__ import annotations

import time
from dataclasses import dataclass, field

from app.models.data_types import Evidence, AnalysisResult, CriticResult, AgentTrace
from app.models.task_graph import TaskGraph


@dataclass
class AgentStep:
    """单个 Agent 执行记录（base_agent.py 使用）"""
    name: str
    duration_ms: int
    summary: str


@dataclass
class AgentContext:
    """Agent 间共享上下文 — 每个 Agent 只写自己负责的字段（禁止覆盖）"""

    # ==================== 输入 ====================
    question: str
    session_id: str | None = None
    document_ids: list[int] | None = None
    history: list[dict] | None = None
    memory_context: str | None = None
    top_k: int = 5
    preferences: dict | None = None

    # ==================== Coordinator 写入 ====================
    plan: TaskGraph | None = None

    # ==================== Knowledge 写入 ====================
    evidence: list[Evidence] = field(default_factory=list)
    sources: list[dict] = field(default_factory=list)

    # ==================== Analysis 写入 ====================
    analysis: AnalysisResult | None = None

    # ==================== AnswerGenerator 写入 ====================
    answer: str = ""

    # ==================== Critic 写入 ====================
    critique: str = ""
    need_retry: bool = False
    retry_target: str = "all"
    reflection_count: int = 0  # 兼容旧接口

    # ==================== 执行轨迹 ====================
    traces: list[AgentTrace] = field(default_factory=list)
    steps: list[AgentStep] = field(default_factory=list)
    start_time: float = field(default_factory=time.time)

    # ==================== 兼容字段（内部使用） ====================
    knowledge_chunks: list = field(default_factory=list)
    knowledge_filtered: list = field(default_factory=list)
    knowledge_all_chunks: list = field(default_factory=list)
    tools_called: list[str] = field(default_factory=list)
    is_agg: bool = False

    # ==================== Setter 方法（禁止覆盖） ====================

    def set_evidence(self, evidence: list[Evidence]):
        if self.evidence:
            raise RuntimeError("Evidence already exists — only Knowledge Agent can write this")
        self.evidence = evidence

    def set_sources(self, sources: list[dict]):
        if self.sources:
            raise RuntimeError("Sources already exists — only Knowledge Agent can write this")
        self.sources = sources

    def set_analysis(self, analysis: AnalysisResult):
        if self.analysis is not None:
            raise RuntimeError("Analysis already exists — only Analysis Agent can write this")
        self.analysis = analysis

    def set_answer(self, answer: str):
        self.answer = answer

    def set_critique(self, critique: str, need_retry: bool = False, retry_target: str = "all"):
        self.critique = critique
        self.need_retry = need_retry
        self.retry_target = retry_target

    def add_trace(self, trace: AgentTrace):
        self.traces.append(trace)

    # ==================== 重试时清空（Orchestrator 调用） ====================

    def reset_for_retry(self, target: str):
        """根据 retry_target 清空对应字段，允许重新写入"""
        if target in ("knowledge", "all"):
            self.evidence = []
            self.sources = []
            self.knowledge_chunks = []
            self.knowledge_filtered = []
            self.knowledge_all_chunks = []
        if target in ("analysis", "all"):
            self.analysis = None
        if target in ("generator", "all"):
            self.answer = ""
        if target in ("all"):
            self.is_agg = False
            self.tools_called = []
        # Critic 字段每次重试前清空
        self.critique = ""
        self.need_retry = False
        self.retry_target = "all"

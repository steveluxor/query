from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any


@dataclass
class MCPSession:
    """MCP 工具执行状态，per-session 隔离

    search_ctx: 向后兼容，非 DAG 路径的 SearchContext
    search_contexts: DAG 路径下 task_id → SearchContext 映射（并行 task 隔离）
    """
    session_id: str
    document_ids: list[int] = field(default_factory=list)
    search_ctx: Any = None       # SearchContext (backward compat, non-DAG path)
    search_contexts: dict[str, Any] = field(default_factory=dict)  # task_id -> SearchContext
    analysis_ctx: Any = None     # AnalysisContext
    user_id: str | None = None
    status: str = "active"       # active / expired / closed
    created_at: float = field(default_factory=time.time)
    last_active: float = field(default_factory=time.time)
    metadata: dict = field(default_factory=dict)

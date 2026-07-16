from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any


@dataclass
class MCPSession:
    """MCP 工具执行状态，per-session 隔离"""
    session_id: str
    document_ids: list[int] = field(default_factory=list)
    search_ctx: Any = None       # SearchContext
    analysis_ctx: Any = None     # AnalysisContext
    user_id: str | None = None
    status: str = "active"       # active / expired / closed
    created_at: float = field(default_factory=time.time)
    last_active: float = field(default_factory=time.time)
    metadata: dict = field(default_factory=dict)

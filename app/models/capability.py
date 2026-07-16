from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class AgentCapability:
    """Agent 能力声明 — 供 Registry 和 Planner 使用"""
    name: str                    # "knowledge"
    description: str             # "知识检索，搜索文档、提取事实"
    tools: list[str] = field(default_factory=list)
    writes_to: list[str] = field(default_factory=list)   # 写入 AgentContext 的哪些字段
    requires: list[str] = field(default_factory=list)    # 执行前必须存在的 context 字段

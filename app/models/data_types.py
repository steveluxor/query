from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class Evidence:
    """Knowledge Agent 提取的事实证据"""
    statement: str          # "2024年A产品销量70万"
    source: str             # "sales.xlsx"
    evidence_type: str      # "table" / "text" / "calculation"
    metadata: dict = field(default_factory=dict)  # {"sheet": "Sheet1", "row": 12}


@dataclass
class Calculation:
    """Analysis Agent 的单次计算结果"""
    operation: str          # "sum" / "rank"
    field: str              # "price"
    arguments: dict = field(default_factory=dict)  # {"row_filter": "前10行"}
    result: Any = None      # 5000
    source: str = ""        # "sales.xlsx"


@dataclass
class AnalysisResult:
    """Analysis Agent 的结构化分析输出"""
    calculations: list[Calculation] = field(default_factory=list)
    findings: list[str] = field(default_factory=list)       # ["销量下降30%"]
    conclusions: list[str] = field(default_factory=list)    # ["供应链影响较大"]


@dataclass
class CriticResult:
    """Critic Agent 的审核结果"""
    score: int = 10                     # 1-10
    problems: list[str] = field(default_factory=list)
    need_retry: bool = False
    retry_target: str = "all"           # "knowledge" / "analysis" / "generator" / "all"


@dataclass
class AgentTrace:
    """单个 Agent 的执行轨迹"""
    task_id: str = ""                   # 关联 TaskGraph 中的任务 ID
    agent: str = ""                     # "Knowledge" / "Analysis" / "Generator" / "Critic"
    start_time: str = ""
    end_time: str = ""
    tools_called: list[str] = field(default_factory=list)
    input_summary: str = ""
    output_summary: str = ""

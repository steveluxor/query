from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class AgentOutput:
    """Agent 输出数据条目 — 带元数据的数据交换单位"""
    value: Any
    producer: str = ""           # 生产者 Agent 名称
    version: int = 1             # 写入次数（自动递增）
    timestamp: float = 0.0       # 写入时间（自动记录）
    metadata: dict = field(default_factory=dict)


@dataclass
class Evidence:
    """提取 Agent 输出的事实证据"""
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
    retry_target: str = "all"           # "retrieval" / "analysis" / "generator" / "all"


@dataclass
class RetrievalReport:
    """检索完整性报告"""
    sources: list[str] = field(default_factory=list)        # 搜索到的文档名列表
    total_chunks: int = 0                                    # 命中文档的全量 chunk 数
    returned_chunks: int = 0                                 # 实际返回到 selected 的 chunk 数
    is_complete: bool = False                                # 数据是否完整（read_all_rows 已调 = True）
    read_all_rows_called: bool = False                       # 是否调用了 read_all_rows
    searches_performed: int = 0                              # 搜索次数


@dataclass
class AgentResult:
    """Agent 执行结果 — Runtime 统一处理 outputs（持久数据）和 actions（控制信号）

    - outputs: 持久化到 context.outputs 的数据（evidence, analysis, answer 等）
    - actions: 一次性 Runtime 控制事件（retry, terminate 等），不落 context
    """
    outputs: dict[str, Any] = field(default_factory=dict)
    actions: list = field(default_factory=list)  # list[ControlAction] (避免循环导入)


@dataclass
class DocumentChunk:
    """文档切片 — 检索结果的最小单位"""
    source: str          # 文件名
    content: str         # chunk 文本
    chunk_index: int = 0     # 在文档中的序号
    total_chunks: int = 0    # 该文档的总 chunk 数


@dataclass
class DocumentBundle:
    """文档包 — RetrievalAgent 的输出，保持 chunk 级粒度"""
    chunks: list[DocumentChunk] = field(default_factory=list)


@dataclass
class KnowledgeObject:
    """知识对象 — 从文档中提取的结构化语义信息"""
    topic: str              # 主题/实体名（如"实验一"）
    attributes: dict = field(default_factory=dict)  # 结构化属性
    source: str = ""         # 来源文档
    confidence: float = 1.0  # 提取置信度


@dataclass
class AgentTrace:
    """单个 Agent 的执行轨迹"""
    task_id: str = ""                   # 关联 TaskGraph 中的任务 ID
    agent: str = ""                     # "Retrieval" / "Extractor" / "Analysis" / "Generator" / "Critic"
    start_time: str = ""
    end_time: str = ""
    tools_called: list[str] = field(default_factory=list)
    input_summary: str = ""
    output_summary: str = ""

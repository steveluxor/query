from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class AgentCapability:
    """Agent 能力声明 — 供 Registry、Planner、Runtime 使用

    inputs:          Agent 输入端口声明（端口名 → 类型）
    required_inputs: 必须通过 port_bindings 连接的端口名集合（不在其中的视为可选）
    outputs:         该 Agent 执行后会写入 context.outputs 的数据 key → 类型映射
    merge_policy: retry 时各 output 的合并策略 (replace / dedup / append)
    control_actions:  Runtime 可执行的 control action（如 retry）
    control_outputs:  Runtime 控制信号 key，不可被 Executor 消费
    terminal:         为 True 时该 Controller 可终止整个 Workflow
    """
    name: str
    description: str = ""
    inputs: dict[str, type] = field(default_factory=dict)
    required_inputs: set[str] = field(default_factory=set)
    outputs: dict[str, Any] = field(default_factory=dict)
    tools: list[str] = field(default_factory=list)
    merge_policy: dict[str, str] = field(default_factory=dict)

    control_actions: list[str] = field(default_factory=list)
    control_outputs: list[str] = field(default_factory=list)
    terminal: bool = False
    allow_root_controller: bool = False

    @property
    def output_keys(self) -> list[str]:
        """所有 outputs 的 key 列表（用于生命周期管理）"""
        return list(self.outputs.keys())

    @property
    def merged_keys(self) -> list[str]:
        """需要合并（而非直接替换）的 output key"""
        return [k for k, v in self.merge_policy.items() if v in ("dedup", "append")]

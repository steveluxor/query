from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class AgentRole(Enum):
    """Agent 运行时角色"""
    EXECUTOR = "executor"         # 普通数据节点：处理输入、产生输出
    CONTROLLER = "controller"     # 控制节点：可改变 Workflow 行为（retry/terminate）


@dataclass
class AgentCapability:
    """Agent 能力声明 — 供 Registry、Planner、Runtime 使用

    inputs:  该 Agent 执行前必须在 context.outputs 中存在的数据 key
    outputs: 该 Agent 执行后会写入 context.outputs 的数据 key → 类型映射
    merge_policy: retry 时各 output 的合并策略 (replace / dedup / append)

    v6 新增:
      role:             运行时角色 (executor / controller)
      control_actions:  Runtime 可执行的 control action（如 retry）
      control_outputs:  Runtime 控制信号 key，不可被 Executor 消费
      terminal:         为 True 时该 Controller 可终止整个 Workflow
    """
    name: str
    description: str = ""
    outputs: dict[str, Any] = field(default_factory=dict)
    tools: list[str] = field(default_factory=list)
    merge_policy: dict[str, str] = field(default_factory=dict)

    # v6 运行时角色与控制
    role: AgentRole = AgentRole.EXECUTOR
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

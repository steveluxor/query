import logging

from app.models.capability import AgentCapability, AgentRole
from app.models.task_graph import TaskGraph

logger = logging.getLogger(__name__)


class AgentRegistry:
    """Agent 能力描述层 — 管理能力声明、实例绑定、数据流校验"""

    def __init__(self):
        self._capabilities: dict[str, AgentCapability] = {}
        self._instances: dict[str, object] = {}

    # ==================== 注册 ====================

    def register(self, capability: AgentCapability, instance=None):
        self._capabilities[capability.name] = capability
        if instance is not None:
            self._instances[capability.name] = instance
        logger.info("[Registry] 注册 Agent: %s — %s", capability.name, capability.description)

    # ==================== 基础查询 ====================

    def get(self, name: str) -> AgentCapability | None:
        return self._capabilities.get(name)

    def get_agent(self, name: str):
        """获取已注册的 agent 实例"""
        return self._instances.get(name)

    def all_capabilities(self) -> list[AgentCapability]:
        return list(self._capabilities.values())

    def valid_names(self) -> set[str]:
        return set(self._capabilities.keys())

    # ==================== 按角色查找 ====================

    def find_by_role(self, role: AgentRole) -> list[AgentCapability]:
        """查找指定角色的所有 Agent"""
        return [cap for cap in self._capabilities.values() if cap.role == role]

    def find_executors(self) -> list[AgentCapability]:
        return self.find_by_role(AgentRole.EXECUTOR)

    def find_controllers(self) -> list[AgentCapability]:
        return self.find_by_role(AgentRole.CONTROLLER)

    # ==================== 按能力查找 ====================

    def find_by_tool(self, tool_name: str) -> list[AgentCapability]:
        """查找拥有指定工具的所有 Agent"""
        return [cap for cap in self._capabilities.values() if tool_name in cap.tools]

    def find_by_writes(self, field_name: str) -> list[AgentCapability]:
        """查找写入指定 context 字段的所有 Agent"""
        return [cap for cap in self._capabilities.values() if field_name in cap.output_keys]

    # ==================== Agent 能力校验 ====================

    def validate_capabilities(self, plan, layers: dict[str, int]) -> list[str]:
        """校验 Agent 能力合法性：注册 + inputs 前置 + outputs 冲突 + control_actions 契约"""
        errors = []

        # 收集每个 task 产出的 output_key（基于 layer 分组）
        produced_by_layer: dict[int, set[str]] = {}
        for t in plan.tasks:
            cap = self._capabilities.get(t.agent)
            if not cap:
                errors.append(f"Agent '{t.agent}' 未注册")
                continue
            layer = layers.get(t.id, 0)
            produced_by_layer.setdefault(layer, set()).update(cap.output_keys)

        # requires/inputs 校验（layer-based：累计上游产出）
        upstream_outputs: set[str] = set()
        for layer_depth in sorted(produced_by_layer.keys()):
            for t in plan.tasks:
                if layers.get(t.id, 0) != layer_depth:
                    continue
                cap = self._capabilities.get(t.agent)
                if not cap:
                    continue
                for req in cap.inputs:
                    if req not in upstream_outputs:
                        errors.append(f"{t.id} 缺少输入 '{req}'（无上游产出）")
            upstream_outputs.update(produced_by_layer.get(layer_depth, set()))

        # 输出冲突检测（同层 task 写同一 output_key — 允许但警告）
        for layer_depth, produced in produced_by_layer.items():
            writes_in_layer: dict[str, list[str]] = {}
            for t in plan.tasks:
                if layers.get(t.id, 0) != layer_depth:
                    continue
                cap = self._capabilities.get(t.agent)
                if not cap:
                    continue
                for field in cap.output_keys:
                    writes_in_layer.setdefault(field, []).append(t.id)
            for field, writers in writes_in_layer.items():
                if len(writers) > 1:
                    logger.warning("输出冲突: %s 都写入 '%s'（多 task 将自动合并）", writers, field)

        # control_actions 契约校验
        for t in plan.tasks:
            cap = self._capabilities.get(t.agent)
            if cap and cap.control_actions:
                # 校验 Planner 没有直接在 task 中引用 control_action（应由 Controller 在运行时决定）
                for key in ("action", "control_action", "retry_target"):
                    if key in t.__dict__ and t.__dict__.get(key):
                        errors.append(f"{t.id} Planner 不应指定 control action '{key}'，应由 Controller 运行时决定")

        return errors

    # ==================== Prompt 生成 ====================

    def format_executors_for_prompt(self) -> str:
        """生成 Executor Agent prompt 片段"""
        lines = []
        for cap in self.find_executors():
            inputs_str = ", ".join(cap.inputs) if cap.inputs else "无"
            outputs_str = ", ".join(cap.output_keys) if cap.output_keys else "无"
            tools_str = ", ".join(cap.tools) if cap.tools else "无"
            lines.append(f"- {cap.name}: {cap.description}")
            lines.append(f"  输入: [{inputs_str}], 输出: [{outputs_str}], 工具: {tools_str}")
        return "\n".join(lines)

    def format_controllers_for_prompt(self) -> str:
        """生成 Controller Agent prompt 片段"""
        lines = []
        for cap in self.find_controllers():
            inputs_str = ", ".join(cap.inputs) if cap.inputs else "无"
            outputs_str = ", ".join(cap.output_keys) if cap.output_keys else "无"
            actions_str = ", ".join(cap.control_actions) if cap.control_actions else "无"
            lines.append(f"- {cap.name}: {cap.description}")
            lines.append(f"  输入: [{inputs_str}], 输出: [{outputs_str}], control_actions: {actions_str}")
        return "\n".join(lines)


def create_default_registry(rag_engine=None) -> AgentRegistry:
    """创建并填充默认 Registry — 所有 Agent 在此注册（含实例化）"""
    from app.core.agents.knowledge_agent import KnowledgeAgent
    from app.core.agents.analysis_agent import AnalysisAgent
    from app.core.agents.critic_agent import CriticAgent
    from app.core.agents.chat_agent import ChatAgent
    from app.core.generator.answer_generator import AnswerGenerator

    # 实例化（依赖 rag_engine 的 Agent 需要传入）
    knowledge = KnowledgeAgent(rag_engine) if rag_engine else KnowledgeAgent.__new__(KnowledgeAgent)
    analysis = AnalysisAgent(rag_engine) if rag_engine else AnalysisAgent.__new__(AnalysisAgent)
    critic = CriticAgent()
    chat = ChatAgent()
    generator = AnswerGenerator()

    registry = AgentRegistry()
    for agent in [knowledge, analysis, critic, chat, generator]:
        if hasattr(agent, 'capability') and agent.capability:
            registry.register(agent.capability, agent)
    return registry

import logging

from app.models.capability import AgentCapability
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

    # ==================== 按能力查找 ====================

    def find_by_tool(self, tool_name: str) -> list[AgentCapability]:
        """查找拥有指定工具的所有 Agent"""
        return [cap for cap in self._capabilities.values() if tool_name in cap.tools]

    def find_by_writes(self, field_name: str) -> list[AgentCapability]:
        """查找写入指定 context 字段的所有 Agent"""
        return [cap for cap in self._capabilities.values() if field_name in cap.writes_to]

    # ==================== DAG 数据流校验 ====================

    def validate_dag(self, plan: TaskGraph) -> list[str]:
        """校验 TaskGraph 的数据流合法性，返回错误列表（空 = 合法）"""
        errors = []
        ids = {t.id for t in plan.tasks}

        # 第一步：基础校验（agent 注册 + 依赖存在性）
        invalid_deps = set()
        for task in plan.tasks:
            cap = self._capabilities.get(task.agent)
            if not cap:
                errors.append(f"Agent '{task.agent}' 未注册")
                continue

            for dep in task.depends_on:
                if dep not in ids:
                    errors.append(f"依赖不存在: {task.id} -> {dep}")
                    invalid_deps.add(dep)

        # 第二步：循环检测（拓扑排序）— 必须在深度计算之前
        in_degree = {t.id: 0 for t in plan.tasks}
        graph = {t.id: [] for t in plan.tasks}
        for t in plan.tasks:
            for dep in t.depends_on:
                if dep in invalid_deps:
                    continue
                graph[dep].append(t.id)
                in_degree[t.id] += 1

        queue = [tid for tid, deg in in_degree.items() if deg == 0]
        visited = 0
        while queue:
            node = queue.pop(0)
            visited += 1
            for neighbor in graph[node]:
                in_degree[neighbor] -= 1
                if in_degree[neighbor] == 0:
                    queue.append(neighbor)

        if visited != len(plan.tasks):
            errors.append("检测到循环依赖")
            return errors  # 有循环时跳过后续校验，避免深度计算无限循环

        # 第三步：输出冲突检测（按 depends_on 深度分层）
        depth = {t.id: 0 for t in plan.tasks}
        changed = True
        while changed:
            changed = False
            for t in plan.tasks:
                for dep in t.depends_on:
                    if dep in invalid_deps:
                        continue
                    if depth[t.id] <= depth[dep]:
                        depth[t.id] = depth[dep] + 1
                        changed = True

        layers: dict[int, list] = {}
        for t in plan.tasks:
            layers.setdefault(depth[t.id], []).append(t)

        for layer_depth, tasks_in_layer in layers.items():
            writes_in_layer: dict[str, list[str]] = {}
            for t in tasks_in_layer:
                cap = self._capabilities.get(t.agent)
                if not cap:
                    continue
                for field in cap.writes_to:
                    writes_in_layer.setdefault(field, []).append(t.id)
            for field, writers in writes_in_layer.items():
                if len(writers) > 1:
                    errors.append(f"输出冲突: {writers} 都写入 '{field}'")

        return errors

    # ==================== Prompt 生成 ====================

    def format_for_prompt(self) -> str:
        """生成 prompt 片段，供 Planner/Coordinator 使用"""
        lines = []
        for cap in self._capabilities.values():
            tools_str = ", ".join(cap.tools) if cap.tools else "无"
            lines.append(f"- {cap.name}: {cap.description}。工具: {tools_str}")
        return "\n".join(lines)


def create_default_registry() -> AgentRegistry:
    """创建并填充默认 Registry — 所有 Agent 在此注册"""
    from app.core.agents.knowledge_agent import KnowledgeAgent
    from app.core.agents.analysis_agent import AnalysisAgent

    registry = AgentRegistry()
    if KnowledgeAgent.capability:
        registry.register(KnowledgeAgent.capability)
    if AnalysisAgent.capability:
        registry.register(AnalysisAgent.capability)
    return registry

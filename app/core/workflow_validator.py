import logging

from app.models.task_graph import TaskGraph

logger = logging.getLogger(__name__)


class WorkflowValidator:
    """DAG 结构校验器 — 检查 TaskGraph 本身的合法性（与 Agent 能力无关）"""

    def validate_structure(self, plan: TaskGraph) -> list[str]:
        """校验图结构：空图 + 依赖存在性 + 循环检测"""
        errors = []

        if not plan.tasks:
            errors.append("TaskGraph 不能为空")
            return errors

        # 1. dependency 存在性
        ids = {t.id for t in plan.tasks}
        invalid_deps = set()
        for task in plan.tasks:
            for dep in task.depends_on:
                if dep not in ids:
                    errors.append(f"依赖不存在: {task.id} -> {dep}")
                    invalid_deps.add(dep)

        # 2. 循环检测（拓扑排序）
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

        return errors

    def get_layers(self, plan: TaskGraph) -> dict[str, int]:
        """按 depends_on 计算每个 task 的深度层"""
        depth = {t.id: 0 for t in plan.tasks}
        changed = True
        while changed:
            changed = False
            for t in plan.tasks:
                for dep in t.depends_on:
                    if depth[t.id] <= depth[dep]:
                        depth[t.id] = depth[dep] + 1
                        changed = True
        return depth


class PolicyValidator:
    """策略层校验器 — Agent 组合与行为合法性（与 Agent 角色/语义相关）"""

    def validate_controller_usage(self, plan: TaskGraph, registry) -> list[str]:
        """Controller 组合合法性校验"""
        from app.models.capability import AgentRole
        errors = []

        if not plan.tasks:
            return errors

        ids = {t.id for t in plan.tasks}

        for task in plan.tasks:
            cap = registry.get(task.agent)
            if not cap:
                continue

            if cap.role != AgentRole.CONTROLLER:
                continue

            # 1. Controller 不能是 DAG 根节点（除非显式允许）
            is_root = not task.depends_on or all(d not in ids for d in task.depends_on)
            if is_root and not cap.allow_root_controller:
                errors.append(f"Controller '{task.id}' 是根节点（无上游依赖），如需此行为请设置 allow_root_controller=True")

            # 2. Controller 的 control_outputs 不可被 Executor 消费
            all_executor_inputs = set()
            for t2 in plan.tasks:
                cap2 = registry.get(t2.agent)
                if cap2 and cap2.role == AgentRole.EXECUTOR:
                    all_executor_inputs.update(cap2.inputs)

            for control_out in cap.control_outputs:
                if control_out in all_executor_inputs:
                    errors.append(f"Controller '{task.id}' 的 control_output '{control_out}' 被 Executor 消费，不允许")

        return errors


class GoalValidator:
    """goal_outputs 校验器 — 两层校验：capability 存在性 + DAG 可达性"""

    def validate_goal_capability(self, plan, registry) -> list[str]:
        """第一层：每个 goal_output 至少有一个 Agent 的 output_keys 包含它"""
        if not plan.goal_outputs:
            return []
        all_keys = set()
        for cap in registry.all_capabilities():
            all_keys.update(cap.output_keys)
        missing = set(plan.goal_outputs) - all_keys
        return [f"goal_output '{m}' 在所有注册 Agent 中均不可达" for m in missing]

    def validate_goal_reachability(self, plan, registry) -> list[str]:
        """第二层：按 DAG 拓扑序传播 outputs，判断当前 DAG 能产出 goal_outputs"""
        if not plan.goal_outputs:
            return []
        validator = WorkflowValidator()
        layers = validator.get_layers(plan)

        node_outputs: dict[str, set[str]] = {}
        for layer_depth in sorted(set(layers.values())):
            for t in plan.tasks:
                if layers.get(t.id) != layer_depth:
                    continue
                cap = registry.get(t.agent)
                if cap:
                    node_outputs[t.id] = set(cap.output_keys)

        all_task_outputs = set()
        for outs in node_outputs.values():
            all_task_outputs.update(outs)

        missing = set(plan.goal_outputs) - all_task_outputs
        return [f"goal_output '{m}' 在当前 DAG 中不可达" for m in missing]

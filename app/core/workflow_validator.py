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

            # Controller 不能是 DAG 根节点（除非显式允许）
            is_root = not task.depends_on or all(d not in ids for d in task.depends_on)
            if is_root and not cap.allow_root_controller:
                errors.append(f"Controller '{task.id}' 是根节点（无上游依赖），如需此行为请设置 allow_root_controller=True")

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
        if missing:
            logger.warning("[GoalValidator] DAG task outputs: %s, goal_outputs: %s, missing: %s",
                           {t.id: list(node_outputs.get(t.id, [])) for t in plan.tasks},
                           plan.goal_outputs, list(missing))
        return [f"goal_output '{m}' 在当前 DAG 中不可达" for m in missing]


class DAGDataFlowValidator:
    """校验 input_mapping 合法性：来源 task 存在 + 在上游 + 有对应 output_key"""

    def _is_ancestor(self, task_id: str, potential_ancestor: str, plan: TaskGraph) -> bool:
        """BFS 检查 potential_ancestor 是否是 task_id 的上游"""
        task_map = {t.id: t for t in plan.tasks}
        visited = set()
        queue = [task_id]
        while queue:
            tid = queue.pop(0)
            if tid in visited:
                continue
            visited.add(tid)
            for dep in task_map[tid].depends_on:
                if dep == potential_ancestor:
                    return True
                queue.append(dep)
        return False

    def validate_input_mapping(self, plan: TaskGraph, registry) -> list[str]:
        """校验 input_mapping：格式 + 来源 task 注册 + 在上游 + output key 存在"""
        errors = []
        task_ids = {t.id for t in plan.tasks}
        task_map = {t.id: t for t in plan.tasks}

        for task in plan.tasks:
            for param_name, source_ref in task.input_mapping.items():
                # 格式校验：必须含 task_id. 前缀
                if "." not in source_ref:
                    errors.append(
                        f"{task.id}.input_mapping['{param_name}']='{source_ref}' 缺少 task_id. 前缀"
                    )
                    continue

                source_task_id, output_key = source_ref.split(".", 1)

                # source_task 存在
                if source_task_id not in task_ids:
                    errors.append(
                        f"{task.id}: input_mapping 引用的上游 task '{source_task_id}' 不存在"
                    )
                    continue

                # source_task 是上游
                if source_task_id not in task.depends_on and not self._is_ancestor(task.id, source_task_id, plan):
                    errors.append(
                        f"{task.id}: input_mapping 引用了非上游 task '{source_task_id}'"
                    )
                    continue

                # output_key 在 source Agent 的能力中
                source_agent = task_map[source_task_id].agent
                source_cap = registry.get(source_agent)
                if not source_cap or output_key not in source_cap.output_keys:
                    errors.append(
                        f"{task.id}: 上游 '{source_task_id}' ({source_agent}) 无 output_key '{output_key}'"
                    )

        return errors

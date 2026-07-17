import asyncio
import logging

from app.core.agent_context import AgentContext, _task_id_var
from app.core.agents.base_agent import ControllerAgent
from app.core.agent_memory import AgentMemory
from app.core.redis_store import RedisStore
from app.core.mcp.client import MCPClient
from app.core.prompt_manager import PromptManager
from app.core.agent_registry import create_default_registry
from app.core.workflow_validator import WorkflowValidator, PolicyValidator
from app.models.capability import AgentRole
from app.models.control import ControlAction
from app.models.task_graph import TaskGraph, TaskNode

logger = logging.getLogger(__name__)


class AgentOrchestrator:
    """编排器：Planner → TaskGraph → Runtime（按 role 分派 Executor/Controller）

    所有请求统一走 Planner 生成 TaskGraph，不再有简单模式 / 规划模式之分。
    Controller 是 DAG 内的一类节点，Runtime 按 role 分派执行。
    """

    def __init__(self, rag_engine, agent_memory: AgentMemory, redis_store: RedisStore, mcp_client: MCPClient):
        self.rag_engine = rag_engine
        self.agent_memory = agent_memory
        self.redis_store = redis_store
        self.mcp_client = mcp_client

        # Agent Registry（能力声明 + 实例绑定，自动完成实例化）
        self.workflow_validator = WorkflowValidator()
        self.policy_validator = PolicyValidator()
        self.registry = create_default_registry(rag_engine=rag_engine)

    async def run(self, context: AgentContext) -> AgentContext:
        # 1. 恢复记忆
        await self._restore_memory(context)

        # 2. 创建 per-request MCP session
        context.mcp_session_id = await self.mcp_client.create_session()

        # 3. 设置文档权限（per-session）
        if context.document_ids:
            await self.mcp_client.call_tool(
                "set_document_ids", {"ids": context.document_ids},
                session_id=context.mcp_session_id,
            )

        try:
            # 4. 偏好检测
            if context.session_id:
                self.agent_memory.update_preferences(context.session_id, context.question)

            # 5. Planner 生成 TaskGraph（任何请求都有有效图）
            plan = self._plan(context.question, context.memory_context)
            if plan and plan.tasks:
                context.plan = plan
                await self._execute_plan(context, plan)

            # 6. 更新记忆
            if context.session_id:
                self._update_memory(context)

        finally:
            # 7. 清理 MCP session 状态
            await self.mcp_client.cleanup_session(context.mcp_session_id)

        return context

    # ==================== DAG 执行 ====================

    async def _execute_plan(self, context: AgentContext, plan: TaskGraph):
        """执行 TaskGraph — 支持 Controller retry 导致的子图重新执行"""
        original_question = context.question
        max_iterations = 10  # 防止 Controller 死循环

        for _ in range(max_iterations):
            # 重置执行状态：从 task 的 status 推导
            completed_ids = {t.id for t in plan.tasks if t.status == "completed"}
            pending = [t for t in plan.tasks if t.status == "pending"]

            if not pending:
                break

            # 拓扑排序执行 ready 任务（并行执行无依赖的 task，task_id 隔离写冲突）
            while pending:
                ready = [t for t in pending if all(d in completed_ids for d in t.depends_on)]
                if not ready:
                    logger.warning("[Orchestrator] 依赖环或无效依赖，跳过剩余 %d 个任务", len(pending))
                    break

                await asyncio.gather(*(self._run_plan_task(
                    context, task, original_question,
                ) for task in ready))

                for task in ready:
                    completed_ids.add(task.id)
                    pending.remove(task)

            # 如果全部完成（没有 Controller 触发 retry），退出
            if not any(t.status == "pending" for t in plan.tasks):
                break

        # 确保 DAG 中有 generator 或 chat 任务产出 answer
        if not context.has_output("answer"):
            logger.info("[Orchestrator] DAG 未产生 answer，执行 Generator 兜底")
            context.current_task_id = "_fallback"
            generator = self.registry.get_agent("generator")
            if generator:
                await generator.execute(context)

    async def _run_plan_task(self, context: AgentContext, task: TaskNode, original_question: str):
        """按 role 分派执行单个 task"""
        cap = self.registry.get(task.agent)
        if not cap:
            logger.warning("[Orchestrator] 未知 agent '%s'，跳过 task %s", task.agent, task.id)
            task.status = "skipped"
            return

        if cap.role == AgentRole.CONTROLLER:
            await self._execute_controller_task(context, task)
        else:
            await self._execute_executor_task(context, task, original_question, cap)

    async def _execute_executor_task(self, context: AgentContext, task: TaskNode, original_question: str, cap):
        """执行 Executor 任务 — 利用 task_id 隔离写，get_output 自动合并"""
        context.question = task.objective
        context.current_task_id = task.id
        _task_id_var.set(task.id)

        agent = self.registry.get_agent(task.agent)

        # 校验前置条件
        if cap.inputs and not context.has_all_outputs(cap.inputs):
            logger.warning("[Orchestrator] 跳过 %s: 缺少 %s", task.id, cap.inputs)
            task.status = "skipped"
            context.question = original_question
            return

        try:
            await agent.execute(
                context, task_id=task.id,
                mcp_client=self.mcp_client, mcp_session_id=context.mcp_session_id,
            )
        finally:
            context.question = original_question

        task.status = "completed"

    async def _execute_controller_task(self, context: AgentContext, task: TaskNode):
        """执行 Controller 任务 — 解析 ControlAction，处理 retry"""
        from app.models.capability import AgentRole

        cap = self.registry.get(task.agent)
        agent = self.registry.get_agent(task.agent)

        if not cap or not agent:
            logger.warning("[Orchestrator] Controller '%s' 未注册", task.agent)
            task.status = "skipped"
            return

        context.current_task_id = task.id
        _task_id_var.set(task.id)

        # 校验前置条件
        if cap.inputs and not context.has_all_outputs(cap.inputs):
            logger.warning("[Orchestrator] Controller %s 缺少 inputs %s", task.id, cap.inputs)
            task.status = "skipped"
            return

        # ControllerAgent 直接返回 ControlAction 列表
        if isinstance(agent, ControllerAgent):
            actions = await agent.execute(
                context, task_id=task.id,
                mcp_client=self.mcp_client, mcp_session_id=context.mcp_session_id,
            )
        else:
            logger.warning("[Orchestrator] Agent %s 未继承 ControllerAgent，无法获取 ControlAction", task.agent)
            task.status = "completed"
            return

        task.status = "completed"

        # 处理 control actions
        for action in actions:
            await self._handle_control_action(context, action)

    async def _handle_control_action(self, context: AgentContext, action: ControlAction):
        """处理 ControlAction — 当前仅支持 retry"""
        plan = context.plan
        if not plan:
            return

        if action.action_type == "retry":
            target_id = action.target_task_id
            if target_id:
                logger.info("[Orchestrator] retry action: target=%s", target_id)
                # 使用 TaskGraph 的 subgraph invalidation（task_id 隔离写，无需清 outputs）
                affected = plan.invalidate_subgraph({target_id})
                logger.info("[Orchestrator] 受影响 %d 个 task: %s", len(affected), sorted(affected))

        elif action.action_type == "terminate":
            logger.info("[Orchestrator] terminate action: 终止执行")
            # 标记所有 pending 任务为 skipped
            for t in plan.tasks:
                if t.status == "pending":
                    t.status = "skipped"

    # ==================== 通用 Merge Runtime ====================

    @staticmethod
    def _merge_outputs(old_value, new_value, policy: str, output_key: str):
        """通用合并策略 — 根据 Capability.merge_policy 决定行为"""
        if policy == "replace":
            return new_value

        elif policy == "append":
            if isinstance(old_value, list) and isinstance(new_value, list):
                return old_value + new_value
            return new_value

        elif policy == "dedup":
            if not isinstance(old_value, list) or not isinstance(new_value, list):
                return new_value
            seen = set()
            for item in old_value:
                seen.add(AgentOrchestrator._dedup_key(item, output_key))
            result = list(old_value)
            for item in new_value:
                key = AgentOrchestrator._dedup_key(item, output_key)
                if key not in seen:
                    seen.add(key)
                    result.append(item)
            return result

        return new_value  # 未知策略 fallback 到 replace

    @staticmethod
    def _dedup_key(item, output_key: str) -> tuple:
        if output_key == "evidence":
            return (getattr(item, 'source', ''), getattr(item, 'statement', '')[:200])
        elif output_key == "sources":
            return ((item.get("file_name", "") if isinstance(item, dict) else ""), str(item)[:200])
        else:
            return (repr(item)[:200],)

    # ==================== Plan-and-Execute ====================

    @staticmethod
    def _parse_json(text: str):
        from app.core.utils import extract_json
        result = extract_json(text)
        if result is None:
            raise ValueError("无法解析 JSON")
        return result

    def _plan(self, question: str, memory_context: str | None = None) -> TaskGraph | None:
        prompt = PromptManager.get("planner", "system")
        prompt = prompt.replace("{available_executors}", self.registry.format_executors_for_prompt())
        prompt = prompt.replace("{available_controllers}", self.registry.format_controllers_for_prompt())
        doc_names = self.rag_engine.vector_store.get_document_names()
        if doc_names:
            doc_list = "\n".join(f"- [{did}] {name}" for did, name in sorted(doc_names.items()))
            prompt += f"\n\n可用文档：\n{doc_list}"
        if memory_context:
            prompt += f"\n\n长期记忆：{memory_context}"
        prompt += f"\n\n用户问题：{question}"

        try:
            result = self.rag_engine.llm.invoke([("human", prompt)])
            data = self._parse_json(result.content)
            if isinstance(data, list):
                return self._list_to_task_graph(data)
            if isinstance(data, dict) and "tasks" in data:
                plan = self._parse_task_graph(data)
                if plan and self._validate_task_graph(plan):
                    return plan
            return None
        except Exception:
            return None

    @staticmethod
    def _parse_task_graph(data: dict) -> TaskGraph | None:
        tasks = []
        for t in data.get("tasks", []):
            tasks.append(TaskNode(
                id=t.get("id", ""),
                agent=t.get("agent", "knowledge"),
                objective=t.get("objective", ""),
                depends_on=t.get("depends_on", []),
                output_key=t.get("output_key", ""),
            ))
        return TaskGraph(goal=data.get("goal", ""), tasks=tasks) if tasks else None

    @staticmethod
    def _list_to_task_graph(items: list) -> TaskGraph | None:
        tasks = []
        for i, item in enumerate(items):
            step = item if isinstance(item, dict) else {"query": str(item)}
            agent = step.get("agent", "knowledge")
            query = step.get("query", "")
            lower = query.lower() if query else ""
            if any(kw in lower for kw in ("calculate_sum", "calculate_rank")):
                agent = "analysis"
            tasks.append(TaskNode(
                id=f"task{i + 1}",
                agent=agent,
                objective=query,
                depends_on=[f"task{i}"] if i > 0 else [],
            ))
        return TaskGraph(goal="", tasks=tasks) if tasks else None

    def _validate_task_graph(self, plan: TaskGraph) -> bool:
        errors = []
        errors += self.workflow_validator.validate_structure(plan)
        if not errors:
            layers = self.workflow_validator.get_layers(plan)
            errors += self.registry.validate_capabilities(plan, layers)
            errors += self.policy_validator.validate_controller_usage(plan, self.registry)
        for err in errors:
            logger.warning("[Orchestrator] TaskGraph 校验失败: %s", err)
        return len(errors) == 0

    # ==================== 记忆管理 ====================

    async def _restore_memory(self, context: AgentContext):
        if not context.session_id:
            return

        if not self.agent_memory.has_session(context.session_id):
            loaded = await self.redis_store.safe_get_memory(context.session_id)
            if loaded:
                self.agent_memory.restore_session(context.session_id, loaded)

        redis_history = await self.redis_store.safe_get_history(context.session_id)
        if redis_history:
            context.history = redis_history

        if context.session_id not in self.agent_memory._sessions and context.history:
            self.agent_memory.rebuild_from_history(
                context.session_id, context.history,
                preferences=context.preferences,
            )

        context.memory_context = self.agent_memory.format_context(context.session_id)

    def _update_memory(self, context: AgentContext):
        source_docs = context.get_output("sources") or []
        doc_names = list(dict.fromkeys(
            s.get("file_name", "") for s in source_docs if s.get("file_name")
        ))
        self.agent_memory.update(
            context.session_id,
            {
                "question": context.question,
                "answer": context.get_output("answer") or "",
                "is_agg": context.is_agg,
                "tools_called": context.tools_called,
                "document_ids": context.document_ids,
                "document_names": doc_names,
            },
        )

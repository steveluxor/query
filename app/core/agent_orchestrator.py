import asyncio
import logging

from app.core.actions import ActionRegistry
from app.core.agent_context import AgentContext, _task_id_var
from app.core.agent_memory import AgentMemory
from app.core.redis_store import RedisStore
from app.core.mcp.client import MCPClient
from app.core.prompt_manager import PromptManager
from app.core.agent_registry import create_default_registry
from app.core.workflow_validator import WorkflowValidator, PolicyValidator, GoalValidator, DAGDataFlowValidator
from app.exceptions import PlannerError, WorkflowValidationError
from app.models.task_graph import TaskGraph, TaskNode, TaskStatus

logger = logging.getLogger(__name__)


class AgentOrchestrator:
    """编排器：Planner → TaskGraph → Runtime（Capability 驱动，不感知 role）

    所有请求统一走 Planner 生成 TaskGraph。
    Runtime 按 Capability 分派，AgentResult 承载 outputs（数据）和 actions（控制信号）。
    Agent 的输出由 AgentResult.outputs 传递，控制信号由 ActionRegistry 分发。
    """

    def __init__(self, rag_engine, agent_memory: AgentMemory, redis_store: RedisStore, mcp_client: MCPClient):
        self.rag_engine = rag_engine
        self.agent_memory = agent_memory
        self.redis_store = redis_store
        self.mcp_client = mcp_client

        # Agent Registry（能力声明 + 实例绑定，自动完成实例化）
        self.workflow_validator = WorkflowValidator()
        self.policy_validator = PolicyValidator()
        self.goal_validator = GoalValidator()
        self.dag_dataflow_validator = DAGDataFlowValidator()
        self.registry = create_default_registry(rag_engine=rag_engine)

        # Action Registry（ControlAction 分发，新增 action type 只需注册 Handler）
        self.action_registry = ActionRegistry().create_default()

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
            # 4. 偏好检测（后台线程执行，不与主流程串行）
            pref_task = None
            if context.session_id:
                pref_task = asyncio.create_task(
                    asyncio.to_thread(
                        self.agent_memory.update_preferences,
                        context.session_id, context.question,
                    )
                )

            # 5. Planner 生成 TaskGraph（与偏好检测并行执行）
            try:
                plan = self._plan(context.question, context.memory_context)
                if not plan or not plan.tasks:
                    plan = self._fallback_plan(context.question)
            except (PlannerError, WorkflowValidationError) as e:
                logger.warning("[Orchestrator] Planner 异常: %s，使用 fallback 计划", e)
                plan = self._fallback_plan(context.question)

            if plan and plan.tasks:
                context.plan = plan
                await self._execute_plan(context, plan)

            # 6. 更新记忆
            if context.session_id:
                self._update_memory(context)

        finally:
            # 确保偏好检测后台任务完成
            if pref_task:
                try:
                    await pref_task
                except Exception as e:
                    logger.warning("偏好检测失败（不影响当前回答）: %s", e)
            # 7. 清理 MCP session 状态
            await self.mcp_client.cleanup_session(context.mcp_session_id)

        return context

    # ==================== DAG 执行 ====================

    async def _execute_plan(self, context: AgentContext, plan: TaskGraph):
        """执行 TaskGraph — 支持 Controller retry 导致的子图重新执行"""
        original_question = context.question
        max_iterations = 10  # 防止 Controller 死循环

        # 从 registry 收集所有 merge_policy，设置到 context
        context.merge_policies = {}
        for cap in self.registry.all_capabilities():
            for key, policy in cap.merge_policy.items():
                context.merge_policies[key] = policy

        for _ in range(max_iterations):
            completed_ids = {t.id for t in plan.tasks if t.status == TaskStatus.COMPLETED}
            pending = [t for t in plan.tasks if t.status in (TaskStatus.PENDING, TaskStatus.RETRYING)]

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
            remaining = [t for t in plan.tasks if t.status in (TaskStatus.PENDING, TaskStatus.RETRYING)]
            if not remaining:
                break

        # 校验 goal_outputs：DAG 执行后必须产出 Planner 声明的所有目标输出
        if plan and plan.goal_outputs:
            missing = [o for o in plan.goal_outputs if not context.has_output(o)]
            if missing:
                from app.exceptions import WorkflowExecutionError
                raise WorkflowExecutionError(f"DAG 未产生目标输出: {missing}")

    async def _run_plan_task(self, context: AgentContext, task: TaskNode, original_question: str):
        """按 capability 驱动统一执行 — 不区分 role，AgentResult 承载 outputs 和 actions"""
        cap = self.registry.get(task.agent)
        if not cap:
            logger.warning("[Orchestrator] 未知 agent '%s'，跳过 task %s", task.agent, task.id)
            task.status = TaskStatus.SKIPPED
            return

        agent = self.registry.get_agent(task.agent)
        if not agent:
            logger.warning("[Orchestrator] agent '%s' 未绑定实例，跳过 task %s", task.agent, task.id)
            task.status = TaskStatus.SKIPPED
            return

        # 按 port_bindings 从指定上游 task 获取数据（唯一数据通道，无 BFS 回退）
        upstream_kwargs = {}
        for port_name, source_ref in task.port_bindings.items():
            if "." not in source_ref:
                logger.warning("[Orchestrator] %s: port_bindings['%s']='%s' 缺少 task_id. 前缀，跳过",
                               task.id, port_name, source_ref)
                continue
            source_task_id, output_key = source_ref.split(".", 1)
            entry = context.get_output_entry(output_key, task_id=source_task_id)
            if entry and entry.value is not None:
                upstream_kwargs[port_name] = entry.value

        context.current_task_id = task.id
        _task_id_var.set(task.id)
        context.question = task.objective

        try:
            result = await agent.execute(
                context, task_id=task.id,
                mcp_client=self.mcp_client, mcp_session_id=context.mcp_session_id,
                **upstream_kwargs,
            )

            # outputs → context（由 Runtime 写入，Agent 只管返回）
            for key, value in result.outputs.items():
                if value is not None:
                    context.set_output(key, value, producer=task.agent)

            # actions → ActionRegistry（新增 action type 只需注册 Handler）
            for action in result.actions:
                await self.action_registry.handle(action, context, self)

            task.status = TaskStatus.COMPLETED
        except Exception as e:
            logger.error("[Orchestrator] task %s 执行失败: %s", task.id, e)
            task.status = TaskStatus.FAILED
            raise
        finally:
            context.question = original_question

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
            if not isinstance(data, dict) or "tasks" not in data:
                raise PlannerError("Planner 输出缺少 'tasks' 字段")
            plan = self._parse_task_graph(data)
            if plan and self._validate_task_graph(plan):
                return plan
            return None
        except (PlannerError, WorkflowValidationError):
            raise
        except Exception as e:
            raise PlannerError(f"Plan 生成异常: {e}") from e

    @staticmethod
    def _parse_task_graph(data: dict) -> TaskGraph | None:
        tasks = []
        for t in data.get("tasks", []):
            agent_name = t.get("agent")
            if not agent_name:
                raise WorkflowValidationError(f"Task '{t.get('id', '?')}' 缺少 agent 字段")
            tasks.append(TaskNode(
                id=t.get("id", ""),
                agent=agent_name,
                objective=t.get("objective", ""),
                depends_on=t.get("depends_on", []),
                output_key=t.get("output_key", ""),
                port_bindings=t.get("port_bindings", {}),
            ))
        return TaskGraph(
            goal=data.get("goal", ""),
            goal_outputs=data.get("goal_outputs", []),
            tasks=tasks,
        ) if tasks else None

    @staticmethod
    def _fallback_plan(question: str) -> TaskGraph:
        """Planner 失败时生成兜底计划：非问候类先检索再生成"""
        lower = question.strip().lower()
        greeting_keywords = ("你好", "hello", "hi", "嗨", "喂", "您好")
        if any(kw in lower for kw in greeting_keywords):
            return TaskGraph(
                goal="",
                goal_outputs=["answer"],
                tasks=[TaskNode(id="task1", agent="chat", objective=question)],
            )
        return TaskGraph(
            goal="",
            goal_outputs=["answer"],
            tasks=[
                TaskNode(id="task1", agent="retrieval", objective=question),
                TaskNode(id="task2", agent="extractor", objective=question,
                         depends_on=["task1"],
                         port_bindings={"knowledge_document": "task1.document_bundle"}),
                TaskNode(id="task3", agent="generator", objective=question,
                         depends_on=["task2"],
                         port_bindings={
                             "structured_knowledge": "task2.knowledge_objects",
                             "evidence_list": "task2.evidence",
                             "source_meta": "task2.sources",
                         }),
            ],
        )

    def _validate_task_graph(self, plan: TaskGraph) -> bool:
        errors = []
        errors += self.workflow_validator.validate_structure(plan)
        if not errors:
            layers = self.workflow_validator.get_layers(plan)
            errors += self.registry.validate_capabilities(plan, layers)
            errors += self.policy_validator.validate_controller_usage(plan, self.registry)
            errors += self.goal_validator.validate_goal_capability(plan, self.registry)
            errors += self.goal_validator.validate_goal_reachability(plan, self.registry)
            errors += self.dag_dataflow_validator.validate_port_bindings(plan, self.registry)
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

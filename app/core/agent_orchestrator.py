import asyncio
import concurrent.futures
import json
import logging

from app.core.agent_context import AgentContext
from app.core.agents.coordinator_agent import CoordinatorAgent
from app.core.agents.knowledge_agent import KnowledgeAgent
from app.core.agents.analysis_agent import AnalysisAgent
from app.core.agents.critic_agent import CriticAgent
from app.core.generator.answer_generator import AnswerGenerator
from app.core.agent_memory import AgentMemory
from app.core.redis_store import RedisStore
from app.core.mcp.client import MCPClient
from app.core.prompt_manager import PromptManager
from app.core.agent_registry import create_default_registry
from app.models.task_graph import TaskGraph, TaskNode

logger = logging.getLogger(__name__)

# Agent 重试字段映射 — 新增 Agent 时在此添加一行
AGENT_RESET_FIELDS: dict[str, list[str]] = {
    "knowledge": ["evidence", "sources"],
    "analysis": ["analysis"],
    "generator": ["answer"],
    "critic": ["critique", "need_retry", "retry_target"],
}


class AgentOrchestrator:
    """编排器：按信息流调度 Agent

    信息流：Coordinator → Knowledge → Analysis → Generate → Critic
    """

    MAX_CRITIC_RETRIES = 2

    def __init__(self, rag_engine, agent_memory: AgentMemory, redis_store: RedisStore, mcp_client: MCPClient):
        self.rag_engine = rag_engine
        self.coordinator = CoordinatorAgent()
        self.knowledge_agent = KnowledgeAgent(rag_engine)
        self.analysis_agent = AnalysisAgent(rag_engine)
        self.critic_agent = CriticAgent()
        self.answer_generator = AnswerGenerator()
        self.agent_memory = agent_memory
        self.redis_store = redis_store
        self.mcp_client = mcp_client

        # Agent Registry（能力声明 + 实例绑定）
        self.registry = create_default_registry()
        self.registry.register(KnowledgeAgent.capability, self.knowledge_agent)
        self.registry.register(AnalysisAgent.capability, self.analysis_agent)

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
            # 4. 偏好检测与其他 Agent 并行
            with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
                pref_future = executor.submit(
                    self.agent_memory.update_preferences, context.session_id, context.question,
                ) if context.session_id else None

                # 5. Coordinator 分类
                await self.coordinator.execute(context)

                # 6. 规划模式：复杂问题拆步骤
                plan = self._plan(context.question, context.memory_context) if self.coordinator.needs_plan else None

                if plan and plan.tasks:
                    logger.info("[Orchestrator] 规划模式触发: goal=%s, 共 %d 步", plan.goal, len(plan.tasks))
                    context.plan = plan
                    await self._execute_plan(context, plan)
                else:
                    # 7. 简单模式：Knowledge → Analysis(可选) → Generate
                    logger.info("[Orchestrator] 简单流程，needs_analysis=%s", self.coordinator.needs_analysis)
                    await self.knowledge_agent.execute(
                        context, mcp_client=self.mcp_client, mcp_session_id=context.mcp_session_id,
                    )

                    if self.coordinator.needs_analysis:
                        await self.analysis_agent.execute(
                            context, mcp_client=self.mcp_client, mcp_session_id=context.mcp_session_id,
                        )

                    self.answer_generator.generate(context)

                # 8. Critic 审核
                if self.coordinator.needs_review:
                    await self._run_critic_with_retry(context)

                # 9. 等待偏好检测完成
                if pref_future:
                    pref_future.result()

            # 10. 更新记忆
            if context.session_id:
                self._update_memory(context)

        finally:
            # 11. 清理 MCP session 状态
            await self.mcp_client.cleanup_session(context.mcp_session_id)

        return context

    # ==================== Critic 审核 + 重试 ====================

    async def _run_critic_with_retry(self, context: AgentContext):
        """Critic 审核，根据 retry_target 决定重跑范围"""
        original_question = context.question

        for attempt in range(self.MAX_CRITIC_RETRIES):
            # 清空上一轮的 critique 状态，允许重新写入
            context.critique = ""
            context.need_retry = False
            context.retry_target = "all"

            await self.critic_agent.execute(context)

            if not context.need_retry:
                logger.info("[Orchestrator] Critic 审核通过 (第 %d 轮)", attempt + 1)
                break

            logger.info("[Orchestrator] Critic 反馈 (第 %d 轮): target=%s, %s",
                        attempt + 1, context.retry_target, context.critique[:100])

            target = context.retry_target
            self.reset_for_retry(context, target)

            # 规划模式：按 task_id 重跑 DAG 子链
            if context.plan and target.startswith("task"):
                context.question = original_question
                await self._retry_from_task(context, target)
            else:
                # 简单模式：按 agent 类型重跑
                if target in ("knowledge", "all"):
                    context.question = original_question
                    await self.knowledge_agent.execute(
                        context, mcp_client=self.mcp_client, mcp_session_id=context.mcp_session_id,
                    )

                if target in ("analysis", "all") and self.coordinator.needs_analysis:
                    await self.analysis_agent.execute(
                        context, mcp_client=self.mcp_client, mcp_session_id=context.mcp_session_id,
                    )

            if target in ("generator", "knowledge", "analysis", "all", *(
                [t.id for t in context.plan.tasks] if context.plan and context.plan.tasks else []
            )):
                context.answer = ""
                self.answer_generator.generate(context)

        if context.need_retry:
            logger.warning("[Orchestrator] Critic 审核未通过，使用最后一轮答案")
            context.answer += "\n\n> 此回答可能不完全准确，建议人工核实。"

        context.question = original_question

    async def _retry_from_task(self, context: AgentContext, task_id: str):
        """从指定 task 开始，重跑该任务及其所有下游依赖"""
        if not context.plan:
            return

        # 找出需要重跑的任务：target task + 所有 transitively dependent tasks
        task_map = {t.id: t for t in context.plan.tasks}
        if task_id not in task_map:
            logger.warning("[Orchestrator] retry 目标 %s 不在 TaskGraph 中", task_id)
            return

        # BFS 找所有下游任务
        to_retry = set()
        queue = [task_id]
        while queue:
            tid = queue.pop(0)
            if tid in to_retry:
                continue
            to_retry.add(tid)
            for t in context.plan.tasks:
                if tid in t.depends_on:
                    queue.append(t.id)

        # 按拓扑顺序排序后执行
        retry_tasks = [t for t in context.plan.tasks if t.id in to_retry]
        retry_tasks.sort(key=lambda t: len(t.depends_on))

        logger.info("[Orchestrator] 从 task %s 重跑 %d 个任务: %s",
                    task_id, len(retry_tasks), [t.id for t in retry_tasks])

        for task in retry_tasks:
            context.question = task.objective
            prev_evidence = list(context.evidence)
            prev_sources = list(context.sources)

            cap = self.registry.get(task.agent)
            agent = self.registry.get_agent(task.agent)

            # 校验前置条件
            if cap and cap.requires and not self._check_requires(context, cap.requires):
                logger.warning("[Orchestrator] 跳过 %s: 缺少前置条件 %s", task.id, cap.requires)
                task.status = "skipped"
                continue

            try:
                if cap:
                    self._clear_fields(context, cap.reset_fields)
                await agent.execute(
                    context, task_id=task.id,
                    mcp_client=self.mcp_client, mcp_session_id=context.mcp_session_id,
                )
            finally:
                context.question = context.question  # restore handled by caller

            if cap and "evidence" not in cap.writes_to:
                context.evidence = prev_evidence + context.evidence
                context.sources = prev_sources + context.sources

            task.status = "completed"
            logger.info("[Orchestrator] 重跑步骤 %s 完成, 累计 evidence=%d",
                        task.id, len(context.evidence))

    # ==================== Agent 调度辅助 ====================

    def reset_for_retry(self, context: AgentContext, target: str):
        """根据 retry_target + AGENT_RESET_FIELDS 声明清空对应字段"""
        if target in AGENT_RESET_FIELDS:
            self._clear_fields(context, AGENT_RESET_FIELDS[target])
        elif target == "all":
            for fields in AGENT_RESET_FIELDS.values():
                self._clear_fields(context, fields)
            context.is_agg = False
            context.tools_called = []
        # target="task1" 等 DAG 模式: 由 _retry_from_task 处理

    @staticmethod
    def _clear_fields(context: AgentContext, fields: list[str]):
        """清空指定的 context 字段"""
        for f in fields:
            if f == "evidence":
                context.evidence = []
            elif f == "sources":
                context.sources = []
            elif f == "analysis":
                context.analysis = None
            elif f == "answer":
                context.answer = ""
            elif f == "critique":
                context.critique = ""
            elif f == "need_retry":
                context.need_retry = False
            elif f == "retry_target":
                context.retry_target = "all"

    @staticmethod
    def _check_requires(context: AgentContext, requires: list[str]) -> bool:
        """校验执行前必须存在的 context 字段"""
        field_values = {
            "evidence": context.evidence,
            "analysis": context.analysis,
            "answer": context.answer,
        }
        for field_name in requires:
            if not field_values.get(field_name):
                return False
        return True

    # ==================== 规划模式 ====================

    async def _execute_plan(self, context: AgentContext, plan: TaskGraph):
        """执行规划模式 — 按依赖拓扑执行任务图，所有 task 共享同一个 session"""
        original_question = context.question
        completed_ids = set()
        pending = list(plan.tasks)

        while pending:
            ready = [t for t in pending if all(d in completed_ids for d in t.depends_on)]
            if not ready:
                logger.warning("[Orchestrator] 依赖环或无效依赖，跳过剩余 %d 个任务", len(pending))
                break

            for task in ready:
                await self._run_plan_task(context, task, original_question)
                completed_ids.add(task.id)
                pending.remove(task)

        self.answer_generator.generate(context)

    async def _run_plan_task(self, context: AgentContext, task: TaskNode, original_question: str):
        """执行单个 plan task"""
        context.question = task.objective
        prev_evidence = list(context.evidence)
        prev_sources = list(context.sources)

        cap = self.registry.get(task.agent)
        agent = self.registry.get_agent(task.agent)

        # 校验前置条件
        if cap and cap.requires and not self._check_requires(context, cap.requires):
            logger.warning("[Orchestrator] 跳过 %s: 缺少前置条件 %s", task.id, cap.requires)
            task.status = "skipped"
            return

        try:
            if cap:
                self._clear_fields(context, cap.reset_fields)
            await agent.execute(
                context, task_id=task.id,
                mcp_client=self.mcp_client, mcp_session_id=context.mcp_session_id,
            )
        finally:
            context.question = original_question

        if cap and "evidence" not in cap.writes_to:
            context.evidence = prev_evidence + context.evidence
            context.sources = prev_sources + context.sources

        task.status = "completed"
        logger.info("步骤 %s 完成: %s, 累计 evidence=%d",
                    task.id, task.objective, len(context.evidence))

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
        prompt = prompt.replace("{available_agents}", self.registry.format_for_prompt())
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
        """兼容旧 flat list 格式：无依赖，顺序执行"""
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
        errors = self.registry.validate_dag(plan)
        for err in errors:
            logger.warning("[Orchestrator] DAG 校验失败: %s", err)
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
        source_docs = context.sources
        doc_names = list(dict.fromkeys(
            s.get("file_name", "") for s in source_docs if s.get("file_name")
        ))
        self.agent_memory.update(
            context.session_id,
            {
                "question": context.question,
                "answer": context.answer,
                "is_agg": context.is_agg,
                "tools_called": context.tools_called,
                "document_ids": context.document_ids,
                "document_names": doc_names,
            },
        )

import asyncio
import logging
import re
import time

from app.core.actions import ActionRegistry
from app.core.agent_context import AgentContext, AgentStep, _task_id_var, _search_ctx_source_var, _task_objective_var
from app.core.agent_memory import AgentMemory
from app.core.infra.redis_store import RedisStore
from app.core.mcp.client import MCPClient
from app.core.prompts.prompt_manager import PromptManager
from app.core.runtime_event_bus import EventType
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

    def __init__(self, llm, agent_memory: AgentMemory, redis_store: RedisStore, mcp_client: MCPClient):
        self.llm = llm
        self.agent_memory = agent_memory
        self.redis_store = redis_store
        self.mcp_client = mcp_client

        # Agent Registry（能力声明 + 实例绑定，自动完成实例化）
        self.workflow_validator = WorkflowValidator()
        self.policy_validator = PolicyValidator()
        self.goal_validator = GoalValidator()
        self.dag_dataflow_validator = DAGDataFlowValidator()
        self.registry = create_default_registry(llm=llm)

        # Action Registry（ControlAction 分发，新增 action type 只需注册 Handler）
        self.action_registry = ActionRegistry().create_default()

    async def run(self, context: AgentContext) -> AgentContext:
        await context.emit(EventType.RUNTIME_STARTED, {
            "session_id": context.session_id,
            "question": context.question,
        })

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
            plan_start = time.time()
            try:
                plan = await self._plan(context.question, context.memory_context, context.history)
                if not plan or not plan.tasks:
                    plan = self._fallback_plan(context.question)
            except (PlannerError, WorkflowValidationError) as e:
                logger.warning("[Orchestrator] Planner 异常: %s，使用 fallback 计划", e)
                plan = self._fallback_plan(context.question)

            # 5a. Plan 后处理：LLM 可能不遵守规则，代码层兜底修正
            if plan and plan.tasks:
                plan = self._post_process_plan(plan, context.question, context.memory_context)

            if plan and plan.tasks:
                context.plan = plan
                # 记录 Planner 步骤到 trace（供前端展示）
                context.steps.append(AgentStep(
                    name="Planner",
                    duration_ms=int((time.time() - plan_start) * 1000),
                    summary=f"生成 {len(plan.tasks)} 节点 DAG",
                ))
                planner_duration = int((time.time() - plan_start) * 1000)
                logger.info("[Orchestrator] Planner 耗时 %dms，生成 %d 节点 DAG",
                            planner_duration, len(plan.tasks))
                await context.emit(EventType.PLAN_GENERATED, {
                    "goal": plan.goal,
                    "planner": {
                        "duration_ms": planner_duration,
                        "summary": f"生成 {len(plan.tasks)} 节点 DAG",
                    },
                    "tasks": [
                        {"id": t.id, "agent": t.agent, "objective": t.objective,
                         "depends_on": t.depends_on, "status": t.status.value}
                        for t in plan.tasks
                    ],
                })
                await self._execute_plan(context, plan)

        finally:
            # 确保偏好检测后台任务完成（所有路径，避免遗留挂起 task）
            if pref_task:
                try:
                    await pref_task
                except Exception as e:
                    logger.warning("偏好检测失败（不影响当前回答）: %s", e)
            # 7. 清理 MCP session 状态
            await self.mcp_client.cleanup_session(context.mcp_session_id)

        # 6. 更新记忆：串行化在偏好检测完成之后，消除并发竞态；to_thread 避免同步 LLM 阻塞事件循环
        if context.session_id:
            await asyncio.to_thread(self._update_memory, context)

        return context

    # ==================== DAG 执行 ====================

    async def _execute_plan(self, context: AgentContext, plan: TaskGraph):
        """执行 TaskGraph — 支持 Controller retry 导致的子图重新执行"""
        original_question = context.question
        max_iterations = 10  # 防止 Controller 死循环
        plan_task_map = {t.id: t for t in plan.tasks}

        # 从 registry 收集所有 merge_policy 和 dedup_key_func，设置到 context
        context.merge_policies = {}
        context.dedup_key_funcs = {}
        for cap in self.registry.all_capabilities():
            for key, policy in cap.merge_policy.items():
                context.merge_policies[key] = policy
            for key, func in cap.dedup_key_func.items():
                context.dedup_key_funcs[key] = func

        # 自动补齐缺失的 port_bindings（基于 capability inputs/outputs 类型匹配）
        self._auto_wire_port_bindings(plan)

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

                results = await asyncio.gather(
                    *(self._run_plan_task(context, task, original_question) for task in ready),
                    return_exceptions=True,
                )
                for task, result in zip(ready, results):
                    pending.remove(task)
                    if isinstance(result, Exception):
                        task.status = TaskStatus.FAILED
                        task.summary = f"失败: {result}"
                        # 失败 → 跳过所有下游任务，避免以空输入继续执行产生"自信但错误"的回答
                        for desc_id in plan.get_descendants(task.id):
                            desc = plan_task_map.get(desc_id)
                            if desc and desc.status in (TaskStatus.PENDING, TaskStatus.RETRYING):
                                desc.status = TaskStatus.SKIPPED
                                desc.summary = f"上游 {task.id} 失败，已跳过"
                                if desc in pending:
                                    pending.remove(desc)
                    else:
                        completed_ids.add(task.id)

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

    def _auto_wire_port_bindings(self, plan: TaskGraph):
        """自动补齐缺失的 port_bindings：通过 capability inputs/outputs 类型匹配推断

        不依赖硬编码的 agent/port 映射，而是根据每个 Agent 声明的输入类型，
        在上游 task 中查找输出类型兼容的 output，自动建立绑定。
        """
        import types as py_types
        import typing

        task_map = {t.id: t for t in plan.tasks}

        def type_matches(source_type: type, target_type: type) -> bool:
            """检查 source_type 是否满足 target_type（复用 DAGDataFlowValidator._type_matches 逻辑）"""
            if source_type is target_type:
                return True
            # Union/Optional: target = X | None, source = X → 匹配
            union_origin = typing.get_origin(target_type)
            if union_origin is py_types.UnionType:
                return any(source_type is arg for arg in typing.get_args(target_type))
            # 参数化泛型: list[KO] == list[KO]
            src_origin = typing.get_origin(source_type)
            tgt_origin = typing.get_origin(target_type)
            if src_origin is not None and tgt_origin is not None:
                return (src_origin is tgt_origin
                        and typing.get_args(source_type) == typing.get_args(target_type))
            # 参数化泛型 → bare 类型: list[dict] 满足 list
            if src_origin is not None and tgt_origin is None:
                return src_origin is target_type
            return False

        for task in plan.tasks:
            task_cap = self.registry.get(task.agent)
            if not task_cap or not task_cap.inputs:
                continue
            for port_name, port_type in task_cap.inputs.items():
                if port_name in task.port_bindings:
                    continue
                # 扫描上游 task 的输出，找类型兼容的第一个
                for dep_id in task.depends_on:
                    dep = task_map.get(dep_id)
                    if not dep:
                        continue
                    dep_cap = self.registry.get(dep.agent)
                    if not dep_cap or not dep_cap.outputs:
                        continue
                    for out_name, out_type in dep_cap.outputs.items():
                        if type_matches(out_type, port_type):
                            task.port_bindings[port_name] = f"{dep_id}.{out_name}"
                            logger.info("[Orchestrator] 自动绑定 %s.%s ← %s.%s (%s → %s)",
                                        task.id, port_name, dep_id, out_name,
                                        out_type.__name__ if hasattr(out_type, '__name__') else str(out_type),
                                        port_type.__name__ if hasattr(port_type, '__name__') else str(port_type))
                            break
                    if port_name in task.port_bindings:
                        break

    def _resolve_search_provider(self, context: AgentContext, task: TaskNode) -> str:
        """找到最近的上游"检索提供者" task_id（capability.tools 含 search_documents）。

        消费者工具（calculate_sum/rank/read_all_rows）不自己搜索，依赖上游检索产生的
        SearchContext。并行分支 DAG 下共享 search_ctx 会被最后一次搜索覆盖，必须按
        依赖关系锁定提供者。数据驱动：通过 capability.tools 判断，不硬编码 agent 名。

        防御性保留：当前 planner（prompts.yaml）所有示例均为单 retrieval DAG，此函数
        恒解析到唯一检索，与共享 search_ctx 等价；仅在 planner 未来生成多并行检索分支
        时才有实际作用，勿删。
        """
        plan = context.plan
        if not plan:
            return ""
        # 自身就是检索提供者（会 search_documents）→ 用自己的 ctx，不绑定上游
        own_cap = self.registry.get(task.agent)
        if own_cap and "search_documents" in own_cap.tools:
            return ""
        task_map = {t.id: t for t in plan.tasks}
        seen: set[str] = set()

        def _walk(tid: str):
            if tid in seen:
                return None
            seen.add(tid)
            dep = task_map.get(tid)
            if not dep:
                return None
            cap = self.registry.get(dep.agent)
            if cap and "search_documents" in cap.tools:
                return dep.id
            for up in dep.depends_on:
                r = _walk(up)
                if r:
                    return r
            return None

        for dep_id in task.depends_on:
            r = _walk(dep_id)
            if r:
                return r
        return ""

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
        # 消费者工具（calculate_sum/rank/read_all_rows）的 search ctx 来源：上游"检索提供者" task_id
        _search_ctx_source_var.set(self._resolve_search_provider(context, task))
        _task_objective_var.set(task.objective)

        task_start = time.time()

        await context.emit(EventType.AGENT_STARTED, {
            "task_id": task.id,
            "agent": task.agent,
            "objective": task.objective,
        })

        try:
            result = await agent.execute(
                context, task_id=task.id,
                mcp_client=self.mcp_client, mcp_session_id=context.mcp_session_id,
                original_question=original_question,
                **upstream_kwargs,
            )

            # 收集 Agent 执行元数据到 TaskNode（供前端 Agent Trace 展示）
            task.duration_ms = int((time.time() - task_start) * 1000)
            task.summary = result.summary
            task.tools_used = result.tools_used
            task.artifacts = result.artifacts

            # outputs → context（由 Runtime 写入，Agent 只管返回）
            for key, value in result.outputs.items():
                if value is not None:
                    context.set_output(key, value, producer=task.agent)

            # actions → ActionRegistry（新增 action type 只需注册 Handler）
            for action in result.actions:
                await context.emit(EventType.CONTROL_ACTION, {
                    "task_id": task.id,
                    "agent": task.agent,
                    "action": action.action_type,
                    "target_task_id": action.target_task_id,
                })
                await self.action_registry.handle(action, context, self)

            task.status = TaskStatus.COMPLETED
            await context.emit(EventType.AGENT_COMPLETED, {
                "task_id": task.id,
                "agent": task.agent,
                "duration_ms": task.duration_ms,
                "summary": task.summary,
                "tools_used": task.tools_used,
            })
            # 收集 Agent 声明的工具名作为执行事实（供 memory 存储）
            if cap.tools:
                context.tools_called.extend(cap.tools)
        except Exception as e:
            task.duration_ms = int((time.time() - task_start) * 1000)
            task.summary = f"失败: {e}"
            logger.error("[Orchestrator] task %s 执行失败: %s", task.id, e)
            task.status = TaskStatus.FAILED
            await context.emit(EventType.AGENT_FAILED, {
                "task_id": task.id,
                "agent": task.agent,
                "duration_ms": task.duration_ms,
                "error": str(e),
            })
            raise

    # ==================== Plan-and-Execute ====================

    @staticmethod
    def _parse_json(text: str):
        from app.core.utils import extract_json
        result = extract_json(text)
        if result is None:
            raise ValueError("无法解析 JSON")
        return result

    async def _plan(self, question: str, memory_context: str | None = None,
              history: list[dict] | None = None) -> TaskGraph | None:
        prompt = PromptManager.get("planner", "system")
        prompt = prompt.replace("{available_executors}", self.registry.format_executors_for_prompt())
        prompt = prompt.replace("{available_controllers}", self.registry.format_controllers_for_prompt())
        doc_names_raw = await self.mcp_client.call_tool("list_documents", {}, session_id="")
        # 解析文档列表为 {id: name} 映射
        doc_names = {}
        if doc_names_raw and "共" in doc_names_raw:
            for line in doc_names_raw.split("\n"):
                if line.startswith("- ["):
                    # 格式: "- [123] 文件名"
                    try:
                        did_str = line.split("[")[1].split("]")[0]
                        name = line.split("] ", 1)[1]
                        doc_names[int(did_str)] = name
                    except (IndexError, ValueError):
                        pass
        if doc_names:
            doc_list = "\n".join(f"- [{did}] {name}" for did, name in sorted(doc_names.items()))
            prompt += f"\n\n可用文档：\n{doc_list}"
        if memory_context:
            prompt += f"\n\n长期记忆：{memory_context}"
            logger.info("[Orchestrator] memory_context (前300字): %s", memory_context[:300])
        # 最近对话历史，帮助 Planner 理解追问上下文
        if history and len(history) > 0:
            recent = history[-5:]
            history_lines = []
            for h in recent:
                if hasattr(h, 'question'):
                    hq, ha = h.question, h.answer
                else:
                    hq, ha = h.get('question', ''), h.get('answer', '')
                history_lines.append(f"用户: {str(hq)[:100]}\n助手: {str(ha)[:200]}")
            prompt += "\n\n最近对话历史：\n" + "\n---\n".join(history_lines)
        # 短追问 + memory 中有数值计算工具名 + 问题本身是延续性追问 → 补全上下文
        if len(question.strip()) < 10 and memory_context and (
            "数值求和" in memory_context or "数值排名" in memory_context
        ):
            continuation_kw = ("结果", "继续", "然后", "接着", "答案", "汇总", "总结",
                               "result", "continue", "next", "then")
            if any(kw in question.strip().lower() for kw in continuation_kw):
                logger.info("[Orchestrator] 检测到短追问+数值计算上下文，补全问题: '%s'", question)
                question = f"{question}（这是对上一轮数值计算的简短追问。注意：需要完整的 retrieval→extractor→analysis→generator 链路，analysis 做精确数值计算，extractor 提取结构化知识，generator 汇总后给出最终结果）"
        prompt += f"\n\n用户问题：{question}"

        try:
            result = self.llm.invoke([("human", prompt)])
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
                         depends_on=["task1"]),
                TaskNode(id="task3", agent="generator", objective=question,
                         depends_on=["task2"]),
            ],
        )

    def _post_process_plan(self, plan: TaskGraph, question: str, memory_context: str | None) -> TaskGraph:
        """Plan 后处理：LLM 可能不遵守规则，代码层兜底修正

        1. 数值问题（求和/排名）必须包含 analysis agent
        2. 纯数值计算问题移除不必要的 extractor
        """
        lower_q = question.strip().lower()
        has_analysis = any(t.agent == "analysis" for t in plan.tasks)

        # === 规则 1：数值问题强制包含 analysis ===
        sum_keywords = ("花了多少钱", "总共", "合计", "总金额", "总和", "求和", "sum", "total",
                        "花了多少", "一共")
        rank_keywords = ("最贵", "最便宜", "排名", "排序", "最高", "最低", "rank", "top")
        # 裸 "第" 会把 "第二个文件"/"第一季度" 误判为排名，仅当 "第N高/低/名/贵/便宜/位" 才算排名
        # 同时覆盖汉字数字（"第二贵""第三名"）
        rank_patterns = (
            r"第\s*\d+\s*(高|低|名|贵|便宜|位)",
            r"第[一二两三四五六七八九十百千]+\s*(高|低|名|贵|便宜|位)",
        )

        needs_sum = any(kw in lower_q for kw in sum_keywords)
        needs_rank = any(kw in lower_q for kw in rank_keywords) or any(re.search(p, lower_q) for p in rank_patterns)

        if (needs_sum or needs_rank) and not has_analysis:
            logger.info("[Orchestrator] 检测到数值问题但无 analysis，自动修正 plan")
            retrieval_task = next((t for t in plan.tasks if t.agent == "retrieval"), None)
            if not retrieval_task:
                max_id = len(plan.tasks) + 1
                analysis_task = TaskNode(
                    id=f"task{max_id}", agent="analysis",
                    objective=question, depends_on=[],
                )
                plan.tasks.append(analysis_task)
            else:
                max_id = len(plan.tasks) + 1
                analysis_task = TaskNode(
                    id=f"task{max_id}", agent="analysis",
                    objective=question,
                    depends_on=[retrieval_task.id],
                )
                plan.tasks.append(analysis_task)

                generator_task = next((t for t in plan.tasks if t.agent == "generator"), None)
                if generator_task:
                    if analysis_task.id not in generator_task.depends_on:
                        generator_task.depends_on.append(analysis_task.id)

        # === 规则 2：纯数值计算问题（求和/排名），移除不必要的 extractor ===
        # Extractor 耗时 90-160s，纯数值问题只需 analysis 结果即可回答
        if (needs_sum or needs_rank):
            extractor_ids = {t.id for t in plan.tasks if t.agent == "extractor"}
            if extractor_ids:
                logger.info("[Orchestrator] 纯数值计算问题，移除 extractor task(s): %s", extractor_ids)
                plan.tasks = [t for t in plan.tasks if t.agent != "extractor"]
                if not plan.tasks:
                    return self._fallback_plan(question)
                # 清理所有引用了已移除 task ID 的 port_bindings
                for t in plan.tasks:
                    if t.agent == "generator":
                        t.depends_on = [d for d in t.depends_on if d not in extractor_ids]
                    stale = [k for k, v in t.port_bindings.items()
                             if "." in v and v.split(".")[0] in extractor_ids]
                    for k in stale:
                        t.port_bindings.pop(k, None)

        # === 规则 3：所有非 chat DAG 必须包含 critic ===
        has_chat_only = len(plan.tasks) == 1 and plan.tasks[0].agent == "chat"
        has_critic = any(t.agent == "critic" for t in plan.tasks)
        if not has_chat_only and not has_critic:
            generator_task = next((t for t in plan.tasks if t.agent == "generator"), None)
            retrieval_task = next((t for t in plan.tasks if t.agent == "retrieval"), None)
            extractor_task = next((t for t in plan.tasks if t.agent == "extractor"), None)
            if generator_task:
                existing_ids = set()
                for t in plan.tasks:
                    try:
                        existing_ids.add(int(t.id.replace("task", "")))
                    except ValueError:
                        pass
                max_id = (max(existing_ids) if existing_ids else 0) + 1
                bindings = {"generated_answer": f"{generator_task.id}.answer"}
                if extractor_task:
                    bindings["evidence_list"] = f"{extractor_task.id}.evidence"
                if retrieval_task:
                    bindings["retrieval_report"] = f"{retrieval_task.id}.retrieval_report"
                critic_task = TaskNode(
                    id=f"task{max_id}", agent="critic",
                    objective="审核答案质量，必要时触发重试",
                    depends_on=[generator_task.id],
                    port_bindings=bindings,
                )
                plan.tasks.append(critic_task)
                logger.info("[Orchestrator] 自动追加 critic task: %s", critic_task.id)

        return plan

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

        # 1. 恢复 memory 快照
        if not self.agent_memory.has_session(context.session_id):
            loaded = await self.redis_store.safe_get_memory(context.session_id)
            if loaded:
                self.agent_memory.restore_session(context.session_id, loaded)

        memory = self.agent_memory._sessions.get(context.session_id)

        # 2. recent_history 缓存满 → 直接用；不满 → 从 Redis 补齐
        if memory and len(memory.recent_history) >= 5:
            context.history = memory.recent_history
        else:
            redis_history = await self.redis_store.safe_get_history(context.session_id)
            if redis_history:
                context.history = redis_history
                if memory:
                    memory.recent_history = redis_history[-5:]

        # 3. 无 memory 且有 history → rebuild（幂等）
        if context.session_id not in self.agent_memory._sessions and context.history:
            self.agent_memory.rebuild_from_history(
                context.session_id, context.history,
                preferences=context.preferences,
            )

        context.memory_context = self.agent_memory.format_context(context.session_id)

    def _update_memory(self, context: AgentContext):
        # 从 registry 收集工具名 → 描述映射（各 Agent 在 capability 中声明）
        tool_desc_map = {}
        for cap in self.registry.all_capabilities():
            tool_desc_map.update(getattr(cap, 'tool_descriptions', {}))

        mapped_tools = [tool_desc_map.get(t, t) for t in context.tools_called]

        source_docs = context.get_output("sources") or []
        doc_names = list(dict.fromkeys(
            s.get("file_name", "") for s in source_docs if s.get("file_name")
        ))
        self.agent_memory.update(
            context.session_id,
            {
                "question": context.question,
                "answer": context.get_output("answer") or "",
                "tools_called": mapped_tools,
                "document_ids": context.document_ids,
                "document_names": doc_names,
            },
        )
        # 追加到 recent_history 缓存
        self.agent_memory.append_turn(
            context.session_id,
            context.question,
            context.get_output("answer") or "",
        )

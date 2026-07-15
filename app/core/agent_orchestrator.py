import concurrent.futures
import json
import logging

from app.core.agent_context import AgentContext
from app.core.agents.coordinator_agent import CoordinatorAgent
from app.core.agents.knowledge_agent import KnowledgeAgent
from app.core.agents.analysis_agent import AnalysisAgent
from app.core.agents.critic_agent import CriticAgent
from app.core.agent_memory import AgentMemory
from app.core.redis_store import RedisStore
from app.mcp_client import MCPClient
from app.prompt_manager import PromptManager

logger = logging.getLogger(__name__)


class AgentOrchestrator:
    """编排器：按 Coordinator 路由结果调度 Agent"""

    MAX_CRITIC_RETRIES = 2

    def __init__(self, rag_engine, agent_memory: AgentMemory, redis_store: RedisStore, mcp_client: MCPClient):
        self.rag_engine = rag_engine
        self.coordinator = CoordinatorAgent()
        self.knowledge_agent = KnowledgeAgent(rag_engine, mcp_client)
        self.analysis_agent = AnalysisAgent(rag_engine, mcp_client)
        self.critic_agent = CriticAgent()
        self.agent_memory = agent_memory
        self.redis_store = redis_store
        self.mcp_client = mcp_client

    async def run(self, context: AgentContext) -> AgentContext:
        # 1. 恢复记忆
        await self._restore_memory(context)

        # 2. 偏好检测与其他 Agent 并行
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
            pref_future = executor.submit(
                self.agent_memory.update_preferences, context.session_id, context.question,
            ) if context.session_id else None

            # 3. Coordinator 分类（LLM 判断）
            await self.coordinator.execute(context)

            # 4. 规划模式：复杂问题拆步骤（仅在需要时调用 Planner LLM）
            plan = self._plan(context.question, context.memory_context) if self.coordinator.needs_plan else []

            if plan:
                # 复杂问题：逐步执行
                logger.info("[Orchestrator] 规划模式触发，共 %d 步: %s", len(plan), plan)
                context.plan = plan
                step_results = []
                current_plan = list(plan)

                for i, step in enumerate(current_plan):
                    step_result = await self._execute_step(step, context)
                    step_results.append({"step": step, "result": step_result, "failed": step_result.startswith("执行失败:")})
                    step_label = f"{step.get('agent', '?')}|{step.get('query', '')}" if isinstance(step, dict) else str(step)[:60]
                    logger.info("步骤 %d/%d 完成: %s", i + 1, len(current_plan), step_label)

                    replan = self._replan(context.question, current_plan, i, step_results)
                    action = replan.get("action", "continue")
                    if action == "finish":
                        logger.info("提前结束规划")
                        break
                    elif action == "replan":
                        current_plan = replan.get("new_plan", current_plan)
                        logger.info("计划调整: %s", current_plan)
                        if i + 1 >= len(current_plan):
                            logger.info("计划已缩短至当前步骤，结束规划")
                            break

                # 基于步骤结果生成最终答案（跳过失败步骤）
                def _step_label(s):
                    return f"{s.get('agent', '?')}|{s.get('query', '')}" if isinstance(s, dict) else str(s)[:60]
                results_text = "\n".join(
                    f"步骤{i+1}[{_step_label(r['step'])}]: {r['result'][:300]}"
                    for i, r in enumerate(step_results) if not r.get("failed")
                )
                if not results_text:
                    context.answer = "抱歉，所有步骤执行失败，请稍后重试。"
                else:
                    system_prompt = PromptManager.get("tool_calling", "system")
                    if context.memory_context:
                        system_prompt += f"\n\n<长期记忆>\n{context.memory_context}\n</长期记忆>"
                    final_prompt = f"{system_prompt}\n\n用户问题：{context.question}\n\n执行结果：\n{results_text}\n\n请基于以上结果回答用户问题。"
                    context.answer = self._invoke_llm(final_prompt, fallback_context=results_text)
            else:
                # 简单问题：原有流程
                logger.info("[Orchestrator] 简单流程，needs_analysis=%s", self.coordinator.needs_analysis)
                # 5. Knowledge Agent（搜索）
                await self.knowledge_agent.execute(context)

                # 6. Analysis Agent（计算）— 仅在需要时执行
                if self.coordinator.needs_analysis:
                    await self.analysis_agent.execute(context)

            # 7. Critic Agent（审核 + 重试）— 仅在需要时执行
            original_question = context.question
            if self.coordinator.needs_review:
                logger.info("[Orchestrator] Critic 审核触发")
                critic_passed = False
                for attempt in range(self.MAX_CRITIC_RETRIES):
                    await self.critic_agent.execute(context)
                    if not context.critique:
                        logger.info("[Orchestrator] Critic 审核通过 (第 %d 轮)", attempt + 1)
                        critic_passed = True
                        break
                    logger.info("[Orchestrator] Critic 反馈 (第 %d 轮): %s", attempt + 1, context.critique[:100])

                    if context.plan:
                        # 规划模式：保存旧数据 → 重新规划 → 重新审核
                        saved_answer = context.answer
                        saved_step_results = list(step_results)
                        saved_sources = list(context.sources)
                        saved_tools = list(context.tools_called)
                        saved_chunks = list(context.knowledge_chunks)
                        saved_filtered = list(context.knowledge_filtered)
                        saved_all_chunks = list(context.knowledge_all_chunks)

                        results_summary = "\n".join(
                            f"步骤{i+1}[{sr['step'].get('agent','?')}|{sr['step'].get('query','')}]: {sr['result'][:200]}"
                            for i, sr in enumerate(step_results)
                        )
                        new_plan = self._plan(
                            context.question,
                            memory_context=context.memory_context,
                            critic_feedback=context.critique,
                            previous_results=results_summary,
                        )

                        # 清空旧数据
                        context.sources.clear()
                        context.tools_called.clear()
                        context.knowledge_chunks.clear()
                        context.knowledge_filtered.clear()
                        context.knowledge_all_chunks.clear()
                        context.critique = ""

                        if new_plan:
                            context.plan = new_plan
                            step_results = []
                            for step in new_plan:
                                step_result = await self._execute_step(step, context)
                                step_results.append({"step": step, "result": step_result, "failed": step_result.startswith("执行失败:")})
                            # 重新生成最终答案（跳过失败步骤）
                            new_results_text = "\n".join(
                                f"步骤{i+1}[{sr['step'].get('agent','?')}|{sr['step'].get('query','')}]: {sr['result'][:200]}"
                                for i, sr in enumerate(step_results) if not sr.get("failed")
                            )
                            if not new_results_text:
                                context.answer = "抱歉，所有步骤执行失败，请稍后重试。"
                            else:
                                new_final_prompt = (
                                    f"{PromptManager.get('tool_calling', 'system')}\n\n"
                                    f"用户问题：{context.question}\n\n执行结果：\n{new_results_text}\n\n"
                                    f"请基于以上结果回答用户问题。"
                                )
                                context.answer = self._invoke_llm(new_final_prompt, fallback_context=new_results_text)
                        else:
                            await self.knowledge_agent.execute(context)
                            # new_plan 为空时，基于搜索结果重新生成答案
                            fallback_prompt = f"{PromptManager.get('tool_calling', 'system')}\n\n用户问题：{context.question}\n\n请基于已检索到的内容回答。"
                            sources_text = "\n".join(
                                f"- [{s.get('file_name', '')}] {s.get('content', '')[:200]}"
                                for s in context.sources[:5]
                            )
                            context.answer = self._invoke_llm(fallback_prompt, fallback_context=sources_text)

                        # 重新审核新方案
                        await self.critic_agent.execute(context)
                        if context.critique:
                            # 仍然拒绝 → 恢复旧数据
                            logger.info("[Orchestrator] 重规划后仍被拒绝，恢复旧数据")
                            context.answer = saved_answer
                            step_results = saved_step_results
                            context.sources = saved_sources
                            context.tools_called = saved_tools
                            context.knowledge_chunks = saved_chunks
                            context.knowledge_filtered = saved_filtered
                            context.knowledge_all_chunks = saved_all_chunks
                            context.critique = ""
                        else:
                            critic_passed = True
                            break
                    else:
                        # 简单模式：注入纠正反馈，要求重新搜索并修正
                        context.question = (
                            f"{context.question}\n\n"
                            f"[用户纠正] 上次回答不正确，原因：{context.critique}\n"
                            f"请重新搜索并回答。注意：\n"
                            f"1. 如果某人仅作为文档作者/提交者出现，不要误认为是行为执行者\n"
                            f"2. 如果搜索结果中有空记录（关键字段均为空），不要引用\n"
                            f"3. 基于用户的纠正重新思考，不要重复同样的错误"
                        )
                        await self.knowledge_agent.execute(context)
                        if self.coordinator.needs_analysis:
                            await self.analysis_agent.execute(context)

                    context.critique = ""

                # 重试耗尽后降级提示
                if not critic_passed:
                    logger.warning("[Orchestrator] Critic 审核未通过，使用最后一轮答案")
                    context.answer += "\n\n> 此回答可能不完全准确，建议人工核实。"
            # 恢复原始问题（重试时拼接了反馈，不能存进记忆）
            context.question = original_question

            # 8. 等待偏好检测完成
            if pref_future:
                pref_future.result()

        # 9. 更新记忆
        if context.session_id:
            self._update_memory(context)

        return context

    # ==================== 答案生成 ====================

    def _invoke_llm(self, prompt: str, fallback_context: str = "") -> str:
        """调用 LLM 生成答案，失败时基于已有 sources 降级"""
        try:
            result = self.rag_engine.llm.invoke([("human", prompt)])
            return result.content
        except Exception as e:
            logger.warning("[Orchestrator] 答案生成 LLM 失败: %s", e)
            if fallback_context:
                return f"根据已检索到的内容：\n{fallback_context[:500]}\n\n（注：答案生成时服务暂时不可用，以上为原始检索结果）"
            return "抱歉，答案生成时服务暂时不可用，请稍后重试。"

    # ==================== Plan-and-Execute ====================

    @staticmethod
    def _parse_json(text: str):
        """解析 LLM 返回的 JSON（兼容 markdown 代码块包裹）"""
        if "```" in text:
            text = text.split("```")[1]
            if text.startswith("json"):
                text = text[4:]
            text = text.strip()
        return json.loads(text)

    def _plan(self, question: str, memory_context: str | None = None, critic_feedback: str | None = None, previous_results: str | None = None) -> list[dict]:
        """生成执行计划，简单问题返回空列表"""
        prompt = PromptManager.get("planner", "system")
        doc_names = self.rag_engine.vector_store.get_document_names()
        if doc_names:
            doc_list = "\n".join(f"- [{did}] {name}" for did, name in sorted(doc_names.items()))
            prompt += f"\n\n可用文档：\n{doc_list}"
        if memory_context:
            prompt += f"\n\n长期记忆：{memory_context}"
        if previous_results:
            prompt += f"\n\n上次执行结果：\n{previous_results}"
        if critic_feedback:
            prompt += f"\n\n上次方案被拒绝，原因：{critic_feedback}\n请基于已有结果重新规划，避免上述问题。"
        prompt += f"\n\n用户问题：{question}"

        try:
            result = self.rag_engine.llm.invoke([("human", prompt)])
            plan = self._parse_json(result.content)
            if not isinstance(plan, list):
                return []
            return [self._normalize_step(s) for s in plan]
        except Exception:
            return []

    @staticmethod
    def _normalize_step(step) -> dict:
        """统一步骤格式：字符串 → 字典"""
        if isinstance(step, dict):
            return step
        text = str(step).strip()
        lower = text.lower()
        if any(kw in lower for kw in ("calculate_sum", "calculate_rank", "read_all_rows")):
            return {"agent": "analysis", "query": text}
        return {"agent": "knowledge", "query": text}

    def _replan(self, question: str, plan: list[dict], step_index: int, step_results: list) -> dict:
        """根据执行结果调整计划"""
        results_summary = "\n".join(
            f"步骤{i+1}[{sr['step'].get('agent','?')}|{sr['step'].get('query','')}]: {sr['result'][:200]}"
            for i, sr in enumerate(step_results)
        )
        prompt = PromptManager.get("replanner", "system").format(
            question=question, plan=plan, results=results_summary,
        )
        try:
            result = self.rag_engine.llm.invoke([("human", prompt)])
            replan = self._parse_json(result.content)
            if "new_plan" in replan:
                replan["new_plan"] = [self._normalize_step(s) for s in replan["new_plan"]]
            return replan
        except Exception:
            return {"action": "continue"}

    async def _execute_step(self, step, context: AgentContext) -> str:
        """执行单个步骤：根据 agent 类型调用现有 KnowledgeAgent 或 AnalysisAgent"""
        # 兼容旧格式（字符串）和新格式（字典）
        if isinstance(step, str):
            agent_type, query = "knowledge", step
        else:
            agent_type = step.get("agent", "knowledge")
            query = step.get("query", "")
        original_question = context.question

        try:
            context.question = query
            if agent_type == "analysis":
                await self.analysis_agent.execute(context)
            else:
                await self.knowledge_agent.execute(context)
            return context.answer
        except Exception as e:
            logger.warning("步骤执行失败: %s", e)
            return f"执行失败: {e}"
        finally:
            context.question = original_question

    # ==================== 记忆管理 ====================

    async def _restore_memory(self, context: AgentContext):
        """从 Redis 恢复记忆（复用 qa.py 现有逻辑）"""
        if not context.session_id:
            return

        # 恢复 AgentMemory
        if context.session_id not in self.agent_memory._sessions:
            loaded = await self.redis_store.safe_get_memory(context.session_id)
            if loaded:
                self.agent_memory._sessions[context.session_id] = loaded._sessions[context.session_id]

        # 从 Redis 读取对话历史
        redis_history = await self.redis_store.safe_get_history(context.session_id)
        if redis_history:
            context.history = redis_history

        # Redis 无记忆 + 传了全量历史 → 重建
        if context.session_id not in self.agent_memory._sessions and context.history:
            self.agent_memory.rebuild_from_history(
                context.session_id, context.history,
                preferences=context.preferences,
            )

        # 格式化记忆上下文
        context.memory_context = self.agent_memory.format_context(context.session_id)

    def _update_memory(self, context: AgentContext):
        """更新记忆（事实/里程碑）"""
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

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

logger = logging.getLogger(__name__)


class AgentOrchestrator:
    """编排器：按信息流调度 Agent

    信息流：Coordinator → Knowledge → Analysis → Generate → Critic
    """

    MAX_CRITIC_RETRIES = 2

    def __init__(self, rag_engine, agent_memory: AgentMemory, redis_store: RedisStore, mcp_client: MCPClient):
        self.rag_engine = rag_engine
        self.coordinator = CoordinatorAgent()
        self.knowledge_agent = KnowledgeAgent(rag_engine, mcp_client)
        self.analysis_agent = AnalysisAgent(rag_engine, mcp_client)
        self.critic_agent = CriticAgent()
        self.answer_generator = AnswerGenerator()
        self.agent_memory = agent_memory
        self.redis_store = redis_store
        self.mcp_client = mcp_client

    async def run(self, context: AgentContext) -> AgentContext:
        # 1. 恢复记忆
        await self._restore_memory(context)

        # 2. 设置文档权限
        if context.document_ids:
            await self.mcp_client.call_tool("set_document_ids", {"ids": context.document_ids})

        # 3. 偏好检测与其他 Agent 并行
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
            pref_future = executor.submit(
                self.agent_memory.update_preferences, context.session_id, context.question,
            ) if context.session_id else None

            # 4. Coordinator 分类
            await self.coordinator.execute(context)

            # 5. 规划模式：复杂问题拆步骤
            plan = self._plan(context.question, context.memory_context) if self.coordinator.needs_plan else []

            if plan:
                logger.info("[Orchestrator] 规划模式触发，共 %d 步", len(plan))
                context.plan = plan
                await self._execute_plan(context, plan)
            else:
                # 6. 简单模式：Knowledge → Analysis(可选) → Generate
                logger.info("[Orchestrator] 简单流程，needs_analysis=%s", self.coordinator.needs_analysis)
                await self.knowledge_agent.execute(context)

                if self.coordinator.needs_analysis:
                    await self.analysis_agent.execute(context)

                self.answer_generator.generate(context)

            # 7. Critic 审核
            if self.coordinator.needs_review:
                await self._run_critic_with_retry(context)

            # 8. 等待偏好检测完成
            if pref_future:
                pref_future.result()

        # 9. 更新记忆
        if context.session_id:
            self._update_memory(context)

        return context

    # ==================== Critic 审核 + 重试 ====================

    async def _run_critic_with_retry(self, context: AgentContext):
        """Critic 审核，根据 retry_target 决定重跑范围"""
        original_question = context.question

        for attempt in range(self.MAX_CRITIC_RETRIES):
            await self.critic_agent.execute(context)

            if not context.need_retry:
                logger.info("[Orchestrator] Critic 审核通过 (第 %d 轮)", attempt + 1)
                break

            logger.info("[Orchestrator] Critic 反馈 (第 %d 轮): target=%s, %s",
                        attempt + 1, context.retry_target, context.critique[:100])

            target = context.retry_target
            context.reset_for_retry(target)

            if target in ("knowledge", "all"):
                context.question = original_question
                await self.knowledge_agent.execute(context)

            if target in ("analysis", "all") and self.coordinator.needs_analysis:
                await self.analysis_agent.execute(context)

            if target in ("generator", "knowledge", "analysis", "all"):
                self.answer_generator.generate(context)

        if context.need_retry:
            logger.warning("[Orchestrator] Critic 审核未通过，使用最后一轮答案")
            context.answer += "\n\n> 此回答可能不完全准确，建议人工核实。"

        context.question = original_question

    # ==================== 规划模式 ====================

    async def _execute_plan(self, context: AgentContext, plan: list[dict]):
        """执行规划模式 — 多步 Evidence 累积"""
        for i, step in enumerate(plan):
            agent_type = step.get("agent", "knowledge")
            query = step.get("query", "")

            original_question = context.question
            # 保存前序步骤累积的 evidence/sources
            prev_evidence = list(context.evidence)
            prev_sources = list(context.sources)
            try:
                context.question = query
                # 清空字段，允许 agent 重新写入
                if agent_type == "analysis":
                    context.analysis = None
                    await self.analysis_agent.execute(context)
                else:
                    context.evidence = []
                    context.sources = []
                    await self.knowledge_agent.execute(context)
            finally:
                context.question = original_question

            # 累积：将本步新 evidence 追加到前序结果中
            if agent_type != "analysis":
                context.evidence = prev_evidence + context.evidence
                context.sources = prev_sources + context.sources

            logger.info("步骤 %d/%d 完成: %s, 累计 evidence=%d",
                        i + 1, len(plan), agent_type, len(context.evidence))

        self.answer_generator.generate(context)

    # ==================== Plan-and-Execute ====================

    @staticmethod
    def _parse_json(text: str):
        if "```" in text:
            text = text.split("```")[1]
            if text.startswith("json"):
                text = text[4:]
            text = text.strip()
        return json.loads(text)

    def _plan(self, question: str, memory_context: str | None = None) -> list[dict]:
        prompt = PromptManager.get("planner", "system")
        doc_names = self.rag_engine.vector_store.get_document_names()
        if doc_names:
            doc_list = "\n".join(f"- [{did}] {name}" for did, name in sorted(doc_names.items()))
            prompt += f"\n\n可用文档：\n{doc_list}"
        if memory_context:
            prompt += f"\n\n长期记忆：{memory_context}"
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
        if isinstance(step, dict):
            return step
        text = str(step).strip()
        lower = text.lower()
        if any(kw in lower for kw in ("calculate_sum", "calculate_rank")):
            return {"agent": "analysis", "query": text}
        return {"agent": "knowledge", "query": text}

    # ==================== 记忆管理 ====================

    async def _restore_memory(self, context: AgentContext):
        if not context.session_id:
            return

        if context.session_id not in self.agent_memory._sessions:
            loaded = await self.redis_store.safe_get_memory(context.session_id)
            if loaded:
                self.agent_memory._sessions[context.session_id] = loaded._sessions[context.session_id]

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

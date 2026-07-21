import asyncio
import logging

from app.config import settings
from app.core.agent_context import AgentContext
from app.core.agents.base_agent import BaseAgent
from app.core.prompt_manager import PromptManager
from app.models.data_types import AgentTrace, AnalysisResult, Evidence, KnowledgeObject
from app.models.capability import AgentCapability

logger = logging.getLogger(__name__)


class AnswerGenerator(BaseAgent):
    """答案生成器：基于 evidence + analysis 统一生成最终自然语言回答"""

    name = "Generator"
    capability = AgentCapability(
        name="generator",
        description="基于 evidence 和 analysis 生成最终自然语言回答",
        inputs={
            "structured_knowledge": list[KnowledgeObject],
            "evidence_list": list[Evidence],
            "source_meta": list,
            "analysis_result": AnalysisResult | None,
        },
        required_inputs={"structured_knowledge", "evidence_list", "source_meta"},
        outputs={
            "answer": str,
        },
        merge_policy={
            "answer": "replace",
        },
    )

    def __init__(self):
        from app.core.llm_factory import create_llm
        self.llm = create_llm()

    async def run(self, context: AgentContext, **kwargs) -> AgentContext:
        await self._generate(context, **kwargs)
        return context

    async def _generate(self, context: AgentContext, **kwargs) -> None:
        """基于 evidence/analysis/knowledge_objects 生成最终 answer"""
        import time
        start = time.time()

        knowledge = kwargs.get("structured_knowledge") or []
        if not isinstance(knowledge, list):
            knowledge = []
        evidences = kwargs.get("evidence_list") or []
        if not isinstance(evidences, list):
            evidences = []
        sources = kwargs.get("source_meta") or []
        if not isinstance(sources, list):
            sources = []
        analysis = kwargs.get("analysis_result")

        prompt = self._build_prompt(context, evidence_list=evidences, analysis_result=analysis, source_meta=sources, structured_knowledge=knowledge)

        try:
            result = await self.llm.ainvoke([("human", prompt)])
            context.set_output("answer", result.content, producer="generator")
            logger.info("[Generator] 生成回答完成，长度 %d", len(result.content))
        except Exception as e:
            logger.warning("[Generator] LLM 生成失败: %s", e)
            context.set_output("answer", self._fallback_answer(context, evidences), producer="generator")

        duration = int((time.time() - start) * 1000)
        context.add_trace(AgentTrace(
            agent="Generator",
            start_time=str(int(start * 1000)),
            end_time=str(int(time.time() * 1000)),
            tools_called=[],
            input_summary=f"evidence={len(evidences)}, analysis={'有' if analysis else '无'}",
            output_summary=f"answer_len={len(context.get_output('answer') or '')}",
        ))

    def _build_prompt(self, context: AgentContext, evidence_list=None, analysis_result=None, source_meta=None, structured_knowledge=None) -> str:
        """构建 Generator prompt — 只包含 question/evidence/analysis/sources"""
        parts = [PromptManager.get("generator", "system"), ""]

        # 用户问题
        parts.append(f"用户问题：{context.question}")

        # 知识对象（结构化摘要）
        if structured_knowledge:
            ko_lines = []
            for i, ko in enumerate(structured_knowledge, 1):
                attrs_str = "; ".join(
                    f"{k}={', '.join(str(x) for x in v) if isinstance(v, list) else v}"
                    for k, v in ko.attributes.items()
                )
                ko_lines.append(f"  {i}. [{ko.source}] {ko.topic}: {attrs_str}")
            parts.append(f"\n知识对象：\n" + "\n".join(ko_lines))

        # 证据（原始细节，与知识对象互补）
        if evidence_list:
            evidence_lines = []
            for i, ev in enumerate(evidence_list, 1):
                evidence_lines.append(
                    f"  {i}. [{ev.source}] {ev.statement} (type={ev.evidence_type})"
                )
            parts.append(f"\n证据：\n" + "\n".join(evidence_lines))
        else:
            parts.append("\n证据：无")

        # 分析结果
        if analysis_result:
            a = analysis_result
            if a.calculations:
                calc_lines = []
                for c in a.calculations:
                    calc_lines.append(f"  - {c.operation}({c.field}): {c.result} (from {c.source})")
                parts.append(f"\n计算结果：\n" + "\n".join(calc_lines))
            if a.findings:
                parts.append(f"\n发现：\n" + "\n".join(f"  - {f}" for f in a.findings))
            if a.conclusions:
                parts.append(f"\n结论：\n" + "\n".join(f"  - {c}" for c in a.conclusions))
        else:
            parts.append("\n分析结果：无")

        # 来源
        if source_meta:
            source_lines = [f"  - {s.get('file_name', '')}" for s in source_meta]
            parts.append(f"\n来源：\n" + "\n".join(source_lines))

        parts.append("\n请基于以上信息组织最终回答。")
        return "\n".join(parts)

    def _fallback_answer(self, context: AgentContext, evidence_list=None) -> str:
        """LLM 失败时的降级回答"""
        if evidence_list is None:
            evidence_list = []
        if evidence_list:
            lines = [f"- {ev.statement}" for ev in evidence_list[:5]]
            return "根据检索到的内容：\n" + "\n".join(lines)
        return "抱歉，答案生成时服务暂时不可用，请稍后重试。"

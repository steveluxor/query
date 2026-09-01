import asyncio
import json
import logging

from app.config import settings
from app.core.agent_context import AgentContext
from app.core.agents.base_agent import BaseAgent
from app.core.prompts.prompt_manager import PromptManager
from app.core.runtime_event_bus import EventType
from app.models.data_types import AgentTrace, AnalysisResult, CodeResult, Evidence, KnowledgeObject
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
            "source_meta": list[dict],
            "analysis_result": AnalysisResult | None,
            "code_result": CodeResult | None,
        },
        required_inputs=set(),
        outputs={
            "answer": str,
        },
        merge_policy={
            "answer": "replace",
        },
    )

    def __init__(self):
        from app.core.infra.llm_factory import create_llm
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
        code_result = kwargs.get("code_result")

        prompt = self._build_prompt(context, evidence_list=evidences, analysis_result=analysis, source_meta=sources, structured_knowledge=knowledge, code_result=code_result)

        try:
            full_answer = []
            async for chunk in self.llm.astream([("human", prompt)]):
                if chunk.content:
                    full_answer.append(chunk.content)
                    await context.emit(EventType.TOKEN_CHUNK, {"text": chunk.content})
            answer = "".join(full_answer)
            context.set_output("answer", answer, producer="generator")
            logger.info("[Generator] 生成回答完成，prompt=%d字, answer=%d字",
                        len(prompt), len(answer))
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

    def _build_prompt(self, context: AgentContext, evidence_list=None, analysis_result=None, source_meta=None, structured_knowledge=None, code_result=None) -> str:
        """构建 Generator prompt — 精简版，减少 token 消耗"""
        parts = [PromptManager.get("generator", "system"), ""]

        # 使用 Planner 解析后的完整语义；原始短追问仍保存在 context.question。
        user_question = context.resolved_question or context.question
        parts.append(f"用户问题：{user_question}")

        if context.preferences:
            parts.append("\n用户偏好：" + json.dumps(context.preferences, ensure_ascii=False))
        parts.append("\n回答约束：涉及文档事实、数值或结论时，"
                     "以本轮证据、计算结果和代码执行结果为准。")

        # 知识对象（紧凑格式，token budget 截断 list 值）
        if structured_knowledge:
            ko_lines = []
            for i, ko in enumerate(structured_knowledge[:20], 1):
                attrs = []
                for k, v in ko.attributes.items():
                    if isinstance(v, list):
                        val = self._truncate_list(v, max_items=3, max_tokens=80)
                    else:
                        val = str(v)[:80]
                    attrs.append(f"{k}={val}")
                ko_lines.append(f"  {i}. [{ko.source}] {ko.topic}: {'; '.join(attrs)}")
            parts.append(f"\n知识对象（{len(structured_knowledge)}条）：\n" + "\n".join(ko_lines))

        # 证据（Extractor 已精简，全量传入）
        if evidence_list:
            evidence_lines = [f"  {i}. [{ev.source}] {ev.statement}"
                             for i, ev in enumerate(evidence_list, 1)]
            parts.append(f"\n证据（{len(evidence_list)}条）：\n" + "\n".join(evidence_lines))
        else:
            parts.append("\n证据：无")

        # 分析结果（只传 calculations + conclusions，跳过 findings）
        if analysis_result:
            if analysis_result.calculations:
                calc_lines = [f"  - {c.operation}({c.field})={c.result}"
                             for c in analysis_result.calculations]
                parts.append(f"\n计算结果：\n" + "\n".join(calc_lines))
            if analysis_result.conclusions:
                parts.append(f"\n结论：\n" + "\n".join(f"  - {c}" for c in analysis_result.conclusions))

        # 代码执行结果
        if code_result and code_result.success:
            parts.append(f"\n代码执行结果：")
            if code_result.output is not None:
                parts.append(f"  结果：{code_result.output}")
            if code_result.stdout:
                parts.append(f"  输出：{code_result.stdout}")
            if code_result.image_paths:
                parts.append("  已生成图表，图片将单独展示在回答下方。")
        elif code_result and not code_result.success:
            parts.append(f"\n代码执行失败：{code_result.error}")

        # 来源
        if source_meta:
            source_lines = [f"  - {s.get('file_name', '')}" for s in source_meta]
            parts.append(f"\n来源：\n" + "\n".join(source_lines))

        parts.append("\n请基于以上信息组织最终回答。")
        return "\n".join(parts)

    @staticmethod
    def _truncate_list(items: list, max_items: int = 3, max_tokens: int = 80) -> str:
        """token budget 截断：短字段取前 N 个，长字段按 token 截断"""
        result = []
        total = 0
        for item in items:
            s = str(item)
            if len(result) >= max_items:
                break
            if total + len(s) > max_tokens and result:
                break
            result.append(s)
            total += len(s)
        text = ", ".join(result)
        if len(items) > len(result):
            text += f" 等{len(items)}项"
        return text

    def _fallback_answer(self, context: AgentContext, evidence_list=None) -> str:
        """LLM 失败时的降级回答"""
        if evidence_list is None:
            evidence_list = []
        if evidence_list:
            lines = [f"- {ev.statement}" for ev in evidence_list[:5]]
            return "根据检索到的内容：\n" + "\n".join(lines)
        return "抱歉，答案生成时服务暂时不可用，请稍后重试。"

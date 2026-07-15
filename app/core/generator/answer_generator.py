import logging

from langchain_openai import ChatOpenAI

from app.config import settings
from app.core.agent_context import AgentContext
from app.models.data_types import AgentTrace

logger = logging.getLogger(__name__)

GENERATOR_SYSTEM_PROMPT = """你是一个智能问答助手。根据提供的证据和分析结果，组织成清晰、准确的回答。

规则：
- 回答必须基于提供的证据和分析结果
- 不要编造数据或引用不存在的证据
- 如果证据不足，说明"在已检索到的内容中未找到"
- 保持回答简洁、切题"""


class AnswerGenerator:
    """答案生成器：基于 evidence + analysis 统一生成最终自然语言回答"""

    def __init__(self):
        self.llm = ChatOpenAI(
            api_key=settings.llm_api_key,
            base_url=settings.llm_base_url,
            model=settings.llm_model_name,
            temperature=0.1,
            max_tokens=4096,
            timeout=30,
        )

    def generate(self, context: AgentContext) -> None:
        """基于 context 中的 evidence 和 analysis 生成最终 answer"""
        import time
        start = time.time()

        prompt = self._build_prompt(context)

        try:
            result = self.llm.invoke([("human", prompt)])
            context.set_answer(result.content)
            logger.info("[Generator] 生成回答完成，长度 %d", len(result.content))
        except Exception as e:
            logger.warning("[Generator] LLM 生成失败: %s", e)
            context.set_answer(self._fallback_answer(context))

        duration = int((time.time() - start) * 1000)
        context.add_trace(AgentTrace(
            agent="Generator",
            start_time=str(int(start * 1000)),
            end_time=str(int(time.time() * 1000)),
            tools_called=[],
            input_summary=f"evidence={len(context.evidence)}, analysis={'有' if context.analysis else '无'}",
            output_summary=f"answer_len={len(context.answer)}",
        ))

    def _build_prompt(self, context: AgentContext) -> str:
        """构建 Generator prompt — 只包含 question/evidence/analysis/sources"""
        parts = [GENERATOR_SYSTEM_PROMPT, ""]

        # 用户问题
        parts.append(f"用户问题：{context.question}")

        # 证据
        if context.evidence:
            evidence_lines = []
            for i, ev in enumerate(context.evidence, 1):
                evidence_lines.append(
                    f"  {i}. [{ev.source}] {ev.statement} (type={ev.evidence_type})"
                )
            parts.append(f"\n证据：\n" + "\n".join(evidence_lines))
        else:
            parts.append("\n证据：无")

        # 分析结果
        if context.analysis:
            a = context.analysis
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
        if context.sources:
            source_lines = [f"  - {s.get('file_name', '')}" for s in context.sources]
            parts.append(f"\n来源：\n" + "\n".join(source_lines))

        parts.append("\n请基于以上信息组织最终回答。")
        return "\n".join(parts)

    def _fallback_answer(self, context: AgentContext) -> str:
        """LLM 失败时的降级回答"""
        if context.evidence:
            lines = [f"- {ev.statement}" for ev in context.evidence[:5]]
            return "根据检索到的内容：\n" + "\n".join(lines)
        return "抱歉，答案生成时服务暂时不可用，请稍后重试。"

import json
import logging

from app.config import settings
from app.core.agents.base_agent import ControllerAgent
from app.core.agents.rule_validator import RuleValidator
from app.core.agent_context import AgentContext
from app.core.prompts.prompt_manager import PromptManager
from app.models.data_types import CriticResult, AgentTrace, AnalysisResult, RetrievalReport
from app.models.capability import AgentCapability
from app.models.control import ControlAction

logger = logging.getLogger(__name__)


class CriticAgent(ControllerAgent):
    """Critic Agent：两级审核 — RuleValidator（确定性）+ Slim LLM Critic（语义）"""

    name = "Critic"
    capability = AgentCapability(
        name="critic",
        description="答案质量审核，评估准确性、完整性、来源引用、逻辑一致性",
        inputs={
            "evidence_list": list,
            "generated_answer": str,
            "retrieval_report": RetrievalReport,
            "analysis_result": AnalysisResult | None,
        },
        required_inputs={"evidence_list", "generated_answer", "retrieval_report"},
        outputs={
            "critique": str,
            "need_retry": bool,
            "retry_target": str,
        },
        merge_policy={
            "critique": "replace",
            "need_retry": "replace",
            "retry_target": "replace",
        },
        control_actions=["retry"],
        control_outputs=["need_retry", "retry_target"],
    )

    def __init__(self):
        from app.core.infra.llm_factory import create_llm
        self.llm = create_llm(temperature=0)
        self.rule_validator = RuleValidator()

    def parse_actions(self, context: AgentContext) -> list[ControlAction]:
        need_retry = context.get_output("need_retry", False)
        if not need_retry:
            return []
        target = context.get_output("retry_target", "all")
        return [ControlAction(action_type="retry", target_task_id=target)]

    async def run(self, context: AgentContext, **kwargs) -> AgentContext:
        import time
        start = time.time()

        evidences = kwargs.get("evidence_list", [])
        analysis = kwargs.get("analysis_result")
        answer = kwargs.get("generated_answer", "")

        # ===== 1. RuleValidator 纯确定性检查（<100ms）=====
        rule_result = self.rule_validator.check(answer=answer, analysis=analysis)
        if not rule_result.passed:
            logger.info("[Critic] RuleValidator 拦截: %s", rule_result.problems)
            context.set_output("critique",
                json.dumps(rule_result.problems, ensure_ascii=False), producer="critic")
            context.set_output("need_retry", True, producer="critic")
            context.set_output("retry_target", "generator", producer="critic")
            duration = int((time.time() - start) * 1000)
            context.add_trace(AgentTrace(
                agent="Critic", start_time=str(int(start * 1000)),
                end_time=str(int(time.time() * 1000)),
                tools_called=[], input_summary="rule_check_only",
                output_summary=f"rule_blocked, problems={len(rule_result.problems)}",
            ))
            return context

        # ===== 2. LLM Critic 精审（slim prompt，evidence 全量传入）=====
        prompt = self._build_slim_prompt(
            context=context, evidence_list=evidences,
            analysis_result=analysis, generated_answer=answer,
        )
        try:
            result = await self.llm.ainvoke([("human", prompt)])
            # Token metrics
            if hasattr(result, 'response_metadata') and 'token_usage' in result.response_metadata:
                usage = result.response_metadata['token_usage']
                logger.info("[Critic] tokens: input=%d, output=%d",
                            usage.get('prompt_tokens', 0), usage.get('completion_tokens', 0))
            critic_result = self._parse_result(result.content)
        except Exception as e:
            logger.warning("[Critic] LLM 调用失败: %s", e)
            critic_result = CriticResult(score=0, need_retry=True, retry_target="all",
                                          problems=[f"Critic 调用失败: {e}"])

        context.set_output("critique",
            json.dumps(critic_result.problems, ensure_ascii=False) if critic_result.problems else "",
            producer="critic")
        context.set_output("need_retry", critic_result.need_retry, producer="critic")
        context.set_output("retry_target", critic_result.retry_target, producer="critic")

        if critic_result.need_retry:
            logger.info("[Critic] 答案需要修改 (score=%d, target=%s): %s",
                        critic_result.score, critic_result.retry_target, critic_result.problems)
        else:
            logger.info("[Critic] 答案通过审核 (score=%d)", critic_result.score)

        duration = int((time.time() - start) * 1000)
        context.add_trace(AgentTrace(
            agent="Critic",
            start_time=str(int(start * 1000)),
            end_time=str(int(time.time() * 1000)),
            tools_called=[],
            input_summary=f"evidence={len(evidences)}",
            output_summary=f"score={critic_result.score}, retry={critic_result.need_retry}",
        ))

        return context

    def _build_slim_prompt(self, context: AgentContext, evidence_list=None,
                           analysis_result=None, generated_answer="") -> str:
        """构建 slim Critic prompt — evidence 全量传入（Extractor 已精简）"""
        # evidence 全量（Extractor 精简后每条 50-80 字）
        if evidence_list:
            evidence_text = "\n".join(
                f"  - [{ev.source}] {ev.statement}"
                for ev in evidence_list
            )
        else:
            evidence_text = "  无"

        # analysis 只传核心
        if analysis_result and analysis_result.calculations:
            calc_text = ", ".join(
                f"{c.operation}({c.field})={c.result}"
                for c in analysis_result.calculations
            )
            analysis_text = f"  计算: {calc_text}"
            if analysis_result.conclusions:
                analysis_text += "\n  结论: " + "; ".join(analysis_result.conclusions[:3])
        else:
            analysis_text = "  无"

        return PromptManager.get("critic", "slim_evaluate").format(
            question=context.question,
            answer=generated_answer,
            evidence=evidence_text,
            analysis=analysis_text,
        )

    def _parse_result(self, text: str) -> CriticResult:
        """解析 CriticResult JSON"""
        from app.core.utils import extract_json
        data = extract_json(text)
        if data is None or not isinstance(data, dict):
            return CriticResult(score=0, need_retry=True, retry_target="all",
                                problems=["Critic 输出解析失败"])

        return CriticResult(
            score=data.get("score", 10),
            problems=data.get("problems", []),
            need_retry=data.get("need_retry", False),
            retry_target=data.get("retry_target", "all"),
        )

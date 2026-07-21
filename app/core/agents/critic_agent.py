import json
import logging

from app.config import settings
from app.core.agents.base_agent import ControllerAgent
from app.core.agent_context import AgentContext
from app.core.prompt_manager import PromptManager
from app.models.data_types import CriticResult, AgentTrace, AnalysisResult, RetrievalReport
from app.models.capability import AgentCapability
from app.models.control import ControlAction

logger = logging.getLogger(__name__)


class CriticAgent(ControllerAgent):
    """Critic Agent：审核答案质量，输出 CriticResult，返回 ControlAction"""

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
        from app.core.llm_factory import create_llm
        self.llm = create_llm(temperature=0)

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
        report = kwargs.get("retrieval_report")

        prompt = self._build_prompt(
            context=context,
            evidence_list=evidences,
            analysis_result=analysis,
            generated_answer=answer,
            retrieval_report=report,
        )

        try:
            result = await self.llm.ainvoke([("human", prompt)])
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

    def _build_prompt(self, context: AgentContext, evidence_list=None, analysis_result=None, generated_answer="", retrieval_report=None) -> str:

        # 格式化 evidence
        if evidence_list:
            evidence_text = "\n".join(
                f"  - [{ev.source}] {ev.statement}" for ev in evidence_list
            )
        else:
            evidence_text = "  无"

        # 格式化 analysis
        if analysis_result:
            parts = []
            if analysis_result.calculations:
                parts.append("计算：" + ", ".join(
                    f"{c.operation}({c.field})={c.result}" for c in analysis_result.calculations
                ))
            if analysis_result.findings:
                parts.append("发现：" + "; ".join(analysis_result.findings))
            analysis_text = "  " + "\n  ".join(parts) if parts else "  无"
        else:
            analysis_text = "  无"

        # 格式化任务计划
        if context.plan and context.plan.tasks:
            task_lines = []
            for t in context.plan.tasks:
                deps = f" (依赖: {', '.join(t.depends_on)})" if t.depends_on else ""
                task_lines.append(f"  - [{t.id}] {t.agent}: {t.objective}{deps}")
            task_plan = f"目标: {context.plan.goal}\n" + "\n".join(task_lines)
        else:
            task_plan = "  无（简单模式）"

        # 格式化检索完整性报告
        if retrieval_report:
            report_text = (
                f"  sources: {retrieval_report.sources}\n"
                f"  total_chunks: {retrieval_report.total_chunks}\n"
                f"  returned_chunks: {retrieval_report.returned_chunks}\n"
                f"  is_complete: {retrieval_report.is_complete}\n"
                f"  read_all_rows_called: {retrieval_report.read_all_rows_called}\n"
                f"  searches_performed: {retrieval_report.searches_performed}"
            )
        else:
            report_text = "  无"

        return PromptManager.get("critic", "evaluate").format(
            question=context.question,
            evidence=evidence_text,
            analysis=analysis_text,
            answer=generated_answer,
            retrieval_report=report_text,
            task_plan=task_plan,
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

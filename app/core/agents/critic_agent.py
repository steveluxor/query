import json
import logging

from langchain_openai import ChatOpenAI

from app.config import settings
from app.core.agents.base_agent import BaseAgent
from app.core.agent_context import AgentContext
from app.models.data_types import CriticResult, AgentTrace

logger = logging.getLogger(__name__)

CRITIC_PROMPT = """你是一个答案质量评估员。根据证据和分析结果，判断回答是否准确、完整。

用户问题：{question}

证据：
{evidence}

分析结果：
{analysis}

生成回答：{answer}

任务计划：
{task_plan}

评估标准：
- 准确性：回答是否基于证据，有无编造
- 完整性：是否回答了用户的问题
- 来源引用：是否正确引用了来源
- 逻辑一致性：回答是否与分析结果矛盾

返回 JSON：
- {{"score": 8, "problems": [], "need_retry": false, "retry_target": "all"}} — 答案合格
- {{"score": 4, "problems": ["缺少来源引用"], "need_retry": true, "retry_target": "generator"}} — 需要修改

retry_target 取值：
- "knowledge": 证据提取有问题，需要重新检索（简单模式）
- "analysis": 计算/分析有问题，需要重新分析（简单模式）
- "generator": 表达有问题，只需要重新生成回答
- "all": 严重问题，需要全部重来
- "task1", "task2" ... : 指定任务 ID，重新执行该任务及其后续依赖任务（规划模式）

如果问题出在某个具体任务上，优先指定 task ID 而不是笼统的 agent 类型。

只返回 JSON，不要解释。"""


class CriticAgent(BaseAgent):
    """Critic Agent：审核答案质量，输出 CriticResult"""

    name = "Critic"

    def __init__(self):
        self.llm = ChatOpenAI(
            api_key=settings.llm_api_key,
            base_url=settings.llm_base_url,
            model=settings.llm_model_name,
            temperature=0,
            timeout=30,
        )

    async def run(self, context: AgentContext) -> AgentContext:
        import time
        start = time.time()

        prompt = self._build_prompt(context)

        try:
            result = self.llm.invoke([("human", prompt)])
            critic_result = self._parse_result(result.content)
        except Exception as e:
            logger.warning("[Critic] LLM 调用失败: %s", e)
            critic_result = CriticResult(score=10, need_retry=False)

        context.set_critique(
            critique=json.dumps(critic_result.problems, ensure_ascii=False) if critic_result.problems else "",
            need_retry=critic_result.need_retry,
            retry_target=critic_result.retry_target,
        )

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
            input_summary=f"evidence={len(context.evidence)}",
            output_summary=f"score={critic_result.score}, retry={critic_result.need_retry}",
        ))

        return context

    def _build_prompt(self, context: AgentContext) -> str:
        # 格式化 evidence
        if context.evidence:
            evidence_text = "\n".join(
                f"  - [{ev.source}] {ev.statement}" for ev in context.evidence
            )
        else:
            evidence_text = "  无"

        # 格式化 analysis
        if context.analysis:
            parts = []
            if context.analysis.calculations:
                parts.append("计算：" + ", ".join(
                    f"{c.operation}({c.field})={c.result}" for c in context.analysis.calculations
                ))
            if context.analysis.findings:
                parts.append("发现：" + "; ".join(context.analysis.findings))
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

        return CRITIC_PROMPT.format(
            question=context.question,
            evidence=evidence_text,
            analysis=analysis_text,
            answer=context.answer or "(无回答)",
            task_plan=task_plan,
        )

    def _parse_result(self, text: str) -> CriticResult:
        """解析 CriticResult JSON"""
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            if "```" in text:
                parts = text.split("```")
                for part in parts:
                    part = part.strip()
                    if part.startswith("json"):
                        part = part[4:]
                    try:
                        data = json.loads(part.strip())
                        break
                    except json.JSONDecodeError:
                        continue
                else:
                    return CriticResult(score=10, need_retry=False)
            else:
                start = text.find("{")
                end = text.rfind("}")
                if start != -1 and end > start:
                    try:
                        data = json.loads(text[start:end + 1])
                    except json.JSONDecodeError:
                        return CriticResult(score=10, need_retry=False)
                else:
                    return CriticResult(score=10, need_retry=False)

        return CriticResult(
            score=data.get("score", 10),
            problems=data.get("problems", []),
            need_retry=data.get("need_retry", False),
            retry_target=data.get("retry_target", "all"),
        )

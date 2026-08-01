import re
from dataclasses import dataclass, field

from app.models.data_types import AnalysisResult


@dataclass
class RuleCheckResult:
    passed: bool
    problems: list[str] = field(default_factory=list)


class RuleValidator:
    """纯确定性规则检查，<100ms，无 LLM 调用

    只做字符串/数值判断，不做语义判断。
    语义层面的 citation coverage 交给 slim LLM Critic。
    """

    def check(self, answer: str, analysis: AnalysisResult | None = None) -> RuleCheckResult:
        problems = []

        # 1. 空/过短（<3 字几乎肯定是生成失败）
        if not answer or len(answer.strip()) < 3:
            problems.append("回答过短或为空")

        # 2. 数值一致性（带 normalize）
        if analysis and analysis.calculations:
            for calc in analysis.calculations:
                if calc.result is not None:
                    if not self._value_present_in_answer(calc.result, answer):
                        problems.append(
                            f"计算结果 {calc.field}={calc.result} 未在回答中体现"
                        )

        return RuleCheckResult(passed=len(problems) == 0, problems=problems)

    @staticmethod
    def _normalize_number(value) -> float | None:
        """将各种数值格式统一为 float，用于跨格式比较"""
        if isinstance(value, (int, float)):
            return float(value)
        if isinstance(value, str):
            try:
                return float(value.strip().rstrip("%"))
            except ValueError:
                return None
        return None

    def _value_present_in_answer(self, calc_result, answer: str) -> bool:
        """检查计算结果是否在 answer 中有对应表述

        支持格式：95 / 95.0 / 95% / 0.95 互转
        """
        # 1. 直接字符串包含（快速路径）
        result_str = str(calc_result)
        if result_str in answer:
            return True

        # 2. normalize 后比较
        norm = self._normalize_number(calc_result)
        if norm is None:
            return True  # 无法 normalize，放行

        # 提取 answer 中所有数值 token
        for match in re.finditer(r"-?\d+\.?\d*%?", answer):
            token = match.group()
            answer_norm = self._normalize_number(token)
            if answer_norm is None:
                continue
            # 整数比较（95 == 95.0）
            if norm == answer_norm:
                return True
            # 百分比互转（0.95 == 95%）
            if "%" in token and abs(norm * 100 - answer_norm) < 0.01:
                return True
            if "%" in result_str and abs(norm / 100 - answer_norm) < 0.01:
                return True

        return False

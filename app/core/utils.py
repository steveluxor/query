import json
import logging

logger = logging.getLogger(__name__)


def _repair_json(text: str) -> str:
    """修复 LLM JSON 中未转义的双引号（字符级行走，前瞻判断）"""
    result = []
    in_string = False
    i = 0
    n = len(text)

    while i < n:
        c = text[i]

        # 跳过已转义字符
        if c == '\\' and i + 1 < n:
            result.append(c)
            result.append(text[i + 1])
            i += 2
            continue

        if c == '"':
            if in_string:
                # 前瞻：如果下一个非空白字符是 JSON 结构符，则此 " 是正确闭合
                j = i + 1
                while j < n and text[j] in ' \t\n\r':
                    j += 1
                if j < n and text[j] in ',:;})]':
                    in_string = False
                    result.append(c)
                else:
                    # 字符串值内的未转义双引号
                    result.append('\\"')
            else:
                in_string = True
                result.append(c)
            i += 1
            continue

        # 中文花引号 → 转义 ASCII 引号
        if c in ('“', '”'):
            result.append('\\"')
            i += 1
            continue

        result.append(c)
        i += 1

    return ''.join(result)


def extract_json(text: str) -> dict | list | None:
    """从 LLM 输出中提取 JSON（兼容 markdown 代码块和自由文本）"""
    if text is None:
        return None

    # 1. 直接解析
    try:
        return json.loads(text)
    except (json.JSONDecodeError, TypeError):
        pass

    # 2. markdown 代码块
    if "```" in text:
        for part in text.split("```"):
            part = part.strip()
            if part.startswith("json"):
                part = part[4:]
            try:
                return json.loads(part.strip())
            except (json.JSONDecodeError, TypeError):
                continue

    # 3. {} 或 [] 块提取 — 用深度匹配找到正确配对，失败后尝试修复
    for open_char, close_char in [("{", "}"), ("[", "]")]:
        start = text.find(open_char)
        if start == -1:
            continue
        depth = 0
        end = -1
        for i in range(start, len(text)):
            if text[i] == open_char:
                depth += 1
            elif text[i] == close_char:
                depth -= 1
                if depth == 0:
                    end = i
                    break
        if end > start:
            candidate = text[start:end + 1]
            try:
                return json.loads(candidate)
            except (json.JSONDecodeError, TypeError):
                # Repair 兜底：修复未转义引号后重试
                repaired = _repair_json(candidate)
                if repaired != candidate:
                    try:
                        return json.loads(repaired)
                    except (json.JSONDecodeError, TypeError):
                        pass

    logger.warning("无法从 LLM 输出中提取 JSON")
    return None

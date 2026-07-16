import json
import logging

logger = logging.getLogger(__name__)


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

    # 3. {} 或 [] 块提取 — 用深度匹配找到正确配对
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
            try:
                return json.loads(text[start:end + 1])
            except (json.JSONDecodeError, TypeError):
                continue

    logger.warning("无法从 LLM 输出中提取 JSON")
    return None

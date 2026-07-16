"""extract_json 单元测试"""
import pytest

from app.core.utils import extract_json


class TestExtractJson:
    def test_direct_parse(self):
        assert extract_json('{"a": 1}') == {"a": 1}

    def test_direct_parse_list(self):
        assert extract_json('[1, 2, 3]') == [1, 2, 3]

    def test_markdown_code_block(self):
        text = '一些文字\n```json\n{"key": "value"}\n```\n更多文字'
        result = extract_json(text)
        assert result == {"key": "value"}

    def test_markdown_code_block_without_json_prefix(self):
        text = '```{"x": 42}```'
        result = extract_json(text)
        assert result == {"x": 42}

    def test_brace_extraction(self):
        result = extract_json('根据数据分析，结果是 {"total": 100} 请参考')
        assert result == {"total": 100}

    def test_bracket_extraction(self):
        result = extract_json('返回数据 [1, 2, 3] 如上')
        assert result == [1, 2, 3]

    def test_invalid_returns_none(self):
        assert extract_json("没有JSON的纯文本") is None

    def test_nested_json(self):
        import json
        data = {"outer": {"inner": [1, 2, 3]}}
        # Use json.dumps for proper double-quote format (str() uses single quotes)
        assert extract_json(json.dumps(data)) == data

    def test_empty_string(self):
        assert extract_json("") is None

    def test_none_input(self):
        assert extract_json(None) is None

    def test_malformed_json_in_code_block(self):
        text = '```json\n{invalid}\n```'
        assert extract_json(text) is None

    def test_multiple_code_blocks_takes_first_valid(self):
        text = '```\ninvalid\n```\n```json\n{"ok": true}\n```'
        result = extract_json(text)
        assert result == {"ok": True}

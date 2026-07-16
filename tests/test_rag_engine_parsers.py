"""RAG Engine 辅助函数单元测试"""
import pytest
from unittest.mock import MagicMock

from app.core.rag_engine import RAGEngine, SearchContext


class TestParseRowFilter:
    def setup_method(self):
        self.engine = RAGEngine.__new__(RAGEngine)

    def test_le(self):
        assert self.engine._parse_row_filter("前10行") == ("le", 10)

    def test_ge(self):
        assert self.engine._parse_row_filter("第5行之后") == ("ge", 5)

    def test_ge_alternative(self):
        assert self.engine._parse_row_filter("第3行以后") == ("ge", 3)

    def test_lt(self):
        assert self.engine._parse_row_filter("第8行之前") == ("lt", 8)

    def test_empty_string(self):
        assert self.engine._parse_row_filter("") is None

    def test_no_match(self):
        assert self.engine._parse_row_filter("随便写") is None


class TestParseContentFilter:
    def test_simple(self):
        assert RAGEngine._parse_content_filter("品牌=万代") == ("品牌", "万代")

    def test_with_spaces(self):
        assert RAGEngine._parse_content_filter("类型 = 玩具") == ("类型", "玩具")

    def test_empty_string(self):
        assert RAGEngine._parse_content_filter("") is None

    def test_no_equals(self):
        assert RAGEngine._parse_content_filter("品牌万代") is None


class TestBigrams:
    def test_basic(self):
        assert RAGEngine._bigrams("abc") == {"ab", "bc"}

    def test_chinese(self):
        result = RAGEngine._bigrams("测试文本")
        assert "测试" in result
        assert "试文" in result
        assert "文本" in result

    def test_single_char(self):
        assert RAGEngine._bigrams("a") == set()

    def test_empty(self):
        assert RAGEngine._bigrams("") == set()


class TestIsEmptyRecord:
    def test_empty_all_values(self):
        content = "产品名: \n品牌: \n价格: 100"
        assert RAGEngine._is_empty_record(content) is True

    def test_has_content(self):
        content = "产品名: 超级玩具\n品牌: 万代\n价格: 100"
        assert RAGEngine._is_empty_record(content) is False

    def test_no_content_keys(self):
        content = "价格: 100\n数量: 5"
        assert RAGEngine._is_empty_record(content) is True

    def test_empty_marker(self):
        content = "产品名: (空)\n品牌: "
        assert RAGEngine._is_empty_record(content) is True


class TestDetermineTopK:
    def setup_method(self):
        self.engine = RAGEngine.__new__(RAGEngine)
        self.engine.MIN_GAP_THRESHOLD = 0.05

    def test_few_items(self):
        filtered = [("d1", 0.8), ("d2", 0.85)]
        assert self.engine._determine_top_k(filtered) == 2

    def test_large_gap(self):
        # Large gap should cut at the gap
        filtered = [
            ("d1", 0.5), ("d2", 0.51), ("d3", 0.52),
            ("d4", 0.9), ("d5", 0.91),
        ]
        result = self.engine._determine_top_k(filtered)
        assert result >= 3

    def test_no_gap(self):
        # All similar scores — should return more
        filtered = [(f"d{i}", 0.5 + i * 0.001) for i in range(10)]
        result = self.engine._determine_top_k(filtered)
        assert result > 3


class TestSelectByDiversity:
    def setup_method(self):
        self.engine = RAGEngine.__new__(RAGEngine)

    def test_basic_diversity(self):
        docs = []
        for i in range(6):
            doc = MagicMock()
            doc.metadata = {"document_id": i // 2}  # 3 docs, 2 chunks each
            docs.append((doc, 0.1 * i))
        result = self.engine._select_by_diversity(docs, 4)
        assert len(result) == 4
        # Should have chunks from different docs
        doc_ids = [r[0].metadata["document_id"] for r in result]
        assert len(set(doc_ids)) >= 2

    def test_more_than_available(self):
        docs = [(MagicMock(metadata={"document_id": 0}), 0.1)]
        result = self.engine._select_by_diversity(docs, 5)
        assert len(result) == 1

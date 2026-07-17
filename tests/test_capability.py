"""AgentCapability 单元测试 — outputs/merge_policy"""
from app.models.capability import AgentCapability
from app.models.data_types import Evidence, AnalysisResult


class TestCapabilityProperties:
    def test_output_keys(self):
        cap = AgentCapability(
            name="test",
            outputs={"a": str, "b": int},
        )
        assert cap.output_keys == ["a", "b"]

    def test_merged_keys_dedup(self):
        cap = AgentCapability(
            name="test",
            outputs={"a": str, "b": int},
            merge_policy={"a": "dedup", "b": "replace"},
        )
        assert "a" in cap.merged_keys
        assert "b" not in cap.merged_keys

    def test_merged_keys_append(self):
        cap = AgentCapability(
            name="test",
            merge_policy={"log": "append"},
        )
        assert "log" in cap.merged_keys

    def test_merged_keys_unknown_policy(self):
        cap = AgentCapability(
            name="test",
            merge_policy={"x": "unknown"},
        )
        assert "x" not in cap.merged_keys

    def test_empty_capability(self):
        cap = AgentCapability(name="empty")
        assert cap.output_keys == []
        assert cap.merged_keys == []


class TestMergePolicies:
    """模拟 _merge_outputs 的各种策略"""

    @staticmethod
    def _merge_outputs(old, new, policy, key):
        if policy == "replace":
            return new
        elif policy == "append":
            if isinstance(old, list) and isinstance(new, list):
                return old + new
            return new
        elif policy == "dedup":
            if not isinstance(old, list) or not isinstance(new, list):
                return new
            seen = set()
            for item in old:
                if key == "evidence":
                    seen.add((getattr(item, 'source', ''), getattr(item, 'statement', '')[:200]))
                elif key == "sources":
                    seen.add((item.get("file_name", "") if isinstance(item, dict) else "", str(item)[:200]))
                else:
                    seen.add(repr(item)[:200])
            result = list(old)
            for item in new:
                if key == "evidence":
                    k = (getattr(item, 'source', ''), getattr(item, 'statement', '')[:200])
                elif key == "sources":
                    k = (item.get("file_name", "") if isinstance(item, dict) else "", str(item)[:200])
                else:
                    k = repr(item)[:200]
                if k not in seen:
                    seen.add(k)
                    result.append(item)
            return result
        else:
            return new

    def test_replace_policy(self):
        result = self._merge_outputs("old", "new", "replace", "answer")
        assert result == "new"

    def test_append_policy(self):
        result = self._merge_outputs([1, 2], [3, 4], "append", "any")
        assert result == [1, 2, 3, 4]

    def test_dedup_evidence(self):
        old = [Evidence(statement="A", source="doc1", evidence_type="text")]
        new = [Evidence(statement="B", source="doc2", evidence_type="text"),
               Evidence(statement="A", source="doc1", evidence_type="text")]  # duplicate
        result = self._merge_outputs(old, new, "dedup", "evidence")
        assert len(result) == 2
        assert result[0].statement == "A"
        assert result[1].statement == "B"

    def test_dedup_sources(self):
        old = [{"file_name": "doc1", "content": "AAA"}]
        new = [{"file_name": "doc1", "content": "AAA"},  # duplicate
               {"file_name": "doc2", "content": "BBB"}]
        result = self._merge_outputs(old, new, "dedup", "sources")
        assert len(result) == 2

    def test_unknown_policy(self):
        result = self._merge_outputs("old", "new", "unknown_policy", "any")
        assert result == "new"

    def test_non_list_append(self):
        result = self._merge_outputs("old", "new", "append", "any")
        assert result == "new"  # fallback to new when not list

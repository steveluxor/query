"""AgentMemory 单元测试 — 会话管理 + 淘汰 + 序列化 + 并发"""
import time
import threading
from unittest.mock import patch, MagicMock

import pytest

from app.core.agent_memory import AgentMemory, SessionMemory, Fact, Milestone


@pytest.fixture
def memory():
    """创建 AgentMemory 实例，mock LLM 避免实际调用"""
    with patch("app.core.llm_factory.create_llm"):
        return AgentMemory(max_sessions=5, idle_ttl=60)


class TestGetOrCreate:
    def test_creates_new_session(self, memory):
        m = memory.get_or_create("s1")
        assert isinstance(m, SessionMemory)
        assert m.session_id == "s1"
        assert m.turn_count == 0

    def test_returns_existing_session(self, memory):
        m1 = memory.get_or_create("s1")
        m2 = memory.get_or_create("s1")
        assert m1 is m2

    def test_updates_last_accessed(self, memory):
        m = memory.get_or_create("s1")
        old_ts = m.last_accessed
        time.sleep(0.01)
        memory.get_or_create("s1")
        assert m.last_accessed >= old_ts


class TestHasSession:
    def test_exists(self, memory):
        memory.get_or_create("s1")
        assert memory.has_session("s1") is True

    def test_not_exists(self, memory):
        assert memory.has_session("unknown") is False


class TestEviction:
    def test_evict_idle_session(self):
        with patch("app.core.llm_factory.create_llm"):
            mem = AgentMemory(max_sessions=100, idle_ttl=0)
        mem.get_or_create("s1")
        time.sleep(0.01)
        # idle_ttl=0 means immediately evictable
        mem.get_or_create("s2")  # triggers eviction
        assert mem.has_session("s1") is False

    def test_evict_lru(self):
        with patch("app.core.llm_factory.create_llm"):
            mem = AgentMemory(max_sessions=3, idle_ttl=9999)
        # Manually insert 4 sessions to exceed max, then call _evict_if_needed
        from app.core.agent_memory import SessionMemory
        ts = 1000.0
        for i in range(4):
            m = SessionMemory(session_id=f"s{i}", created_at=ts, last_accessed=ts)
            with mem._lock:
                mem._sessions[f"s{i}"] = m
            ts += 1.0
        # Now 4 sessions, max=3. LRU should evict s0 (oldest)
        with patch("app.core.agent_memory.time") as mock_time:
            mock_time.time.return_value = ts
            mock_time.time.side_effect = None
            mem._evict_if_needed()
        assert mem.has_session("s0") is False
        assert mem.has_session("s3") is True


class TestToDictRoundtrip:
    def test_roundtrip(self, memory):
        m = memory.get_or_create("s1")
        m.facts.append(Fact(text="用户查询了销售数据", turn_number=1))
        m.milestones.append(Milestone(summary="讨论了销售", start_turn=1, end_turn=1))
        m.preferences = {"address_as": "老板"}
        m.turn_count = 1
        m._dirty = True

        data = memory.to_dict("s1")
        assert data is not None
        assert data["session_id"] == "s1"
        assert len(data["facts"]) == 1
        assert data["facts"][0]["text"] == "用户查询了销售数据"
        assert data["preferences"] == {"address_as": "老板"}

        # Restore
        restored = AgentMemory.from_dict(data, "s1")
        m2 = restored.get_or_create("s1")
        assert m2.facts[0].text == "用户查询了销售数据"
        assert m2.preferences == {"address_as": "老板"}
        assert m2.turn_count == 1

    def test_to_dict_returns_none_when_not_dirty(self, memory):
        memory.get_or_create("s1")
        # New session is dirty by default
        data = memory.to_dict("s1")
        assert data is not None
        # After from_dict, dirty is False
        restored = AgentMemory.from_dict(data, "s1")
        data2 = restored.to_dict("s1")
        assert data2 is None

    def test_to_dict_nonexistent_session(self, memory):
        assert memory.to_dict("nonexistent") is None


class TestFormatContext:
    def test_empty_session(self, memory):
        result = memory.format_context("nonexistent")
        assert result == ""

    def test_with_facts(self, memory):
        m = memory.get_or_create("s1")
        m.facts.append(Fact(text="销售额5000万", turn_number=1))
        result = memory.format_context("s1")
        assert "已知事实" in result
        assert "销售额5000万" in result

    def test_with_preferences(self, memory):
        m = memory.get_or_create("s1")
        m.preferences = {"address_as": "老板"}
        result = memory.format_context("s1")
        assert "用户偏好" in result


class TestRebuildFromHistory:
    def test_rebuild_idempotent(self, memory):
        history = [
            {"question": "q1", "answer": "a1", "is_agg": False},
            {"question": "q2", "answer": "a2", "is_agg": True},
        ]
        memory.rebuild_from_history("s1", history)
        assert memory.get_or_create("s1").turn_count == 2

        # Second call should be no-op
        memory.rebuild_from_history("s1", history)
        assert memory.get_or_create("s1").turn_count == 2

    def test_rebuild_with_preferences(self, memory):
        prefs = {"address_as": "老板"}
        memory.rebuild_from_history("s1", [{"question": "q", "answer": "a"}], preferences=prefs)
        m = memory.get_or_create("s1")
        assert m.preferences == prefs

    def test_rebuild_empty_history(self, memory):
        memory.rebuild_from_history("s1", [])
        assert memory.get_or_create("s1").turn_count == 0


class TestThreadSafety:
    def test_concurrent_get_or_create(self, memory):
        errors = []
        def worker(sid):
            try:
                for _ in range(100):
                    memory.get_or_create(sid)
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=worker, args=(f"s{i}",)) for i in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert errors == []

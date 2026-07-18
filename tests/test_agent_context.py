"""AgentContext 单元测试 — outputs 容器 + 线程安全"""
import concurrent.futures
import threading
import time

import pytest

from app.core.agent_context import AgentContext, AgentStep
from app.models.data_types import Evidence, AnalysisResult, AgentTrace


class TestSetOutput:
    def test_set_output_basic(self):
        ctx = AgentContext(question="q")
        evidence = [Evidence(statement="s", source="f", evidence_type="fact")]
        ctx.set_output("evidence", evidence, producer="knowledge")
        assert ctx.get_output("evidence") == evidence
        assert ctx.has_output("evidence")

    def test_get_output_default(self):
        ctx = AgentContext(question="q")
        assert ctx.get_output("nonexistent") is None
        assert ctx.get_output("nonexistent", []) == []

    def test_get_output_entry_metadata(self):
        ctx = AgentContext(question="q")
        evidence = [Evidence(statement="s", source="f", evidence_type="fact")]
        ctx.set_output("evidence", evidence, producer="knowledge")
        entry = ctx.get_output_entry("evidence")
        assert entry is not None
        assert entry.producer == "knowledge"
        assert entry.version == 1
        assert entry.timestamp > 0

    def test_set_output_version_increment(self):
        ctx = AgentContext(question="q")
        ctx.set_output("answer", "v1", producer="gen")
        assert ctx.get_output_entry("answer").version == 1
        ctx.set_output("answer", "v2", producer="gen")
        assert ctx.get_output_entry("answer").version == 2
        assert ctx.get_output("answer") == "v2"

    def test_clear_outputs(self):
        ctx = AgentContext(question="q")
        ctx.set_output("a", 1)
        ctx.set_output("b", 2)
        ctx.clear_outputs(["a", "b"])
        assert not ctx.has_output("a")
        assert not ctx.has_output("b")

    def test_clear_outputs_partial(self):
        ctx = AgentContext(question="q")
        ctx.set_output("a", 1)
        ctx.set_output("b", 2)
        ctx.clear_outputs(["a"])
        assert not ctx.has_output("a")
        assert ctx.has_output("b")

    def test_output_not_shared_across_contexts(self):
        ctx1 = AgentContext(question="q1")
        ctx2 = AgentContext(question="q2")
        ctx1.set_output("key", "val1")
        ctx2.set_output("key", "val2")
        assert ctx1.get_output("key") == "val1"
        assert ctx2.get_output("key") == "val2"


class TestThreadSafety:
    def test_concurrent_set_output(self):
        ctx = AgentContext(question="q")
        n_threads = 10
        barrier = threading.Barrier(n_threads)

        def _set(key, value):
            barrier.wait()
            ctx.set_output(key, value, producer=f"thread-{key}")

        with concurrent.futures.ThreadPoolExecutor(max_workers=n_threads) as ex:
            futures = [ex.submit(_set, f"k{i}", i) for i in range(n_threads)]
            concurrent.futures.wait(futures)

        for i in range(n_threads):
            assert ctx.get_output(f"k{i}") == i

    def test_concurrent_get_set_no_crash(self):
        ctx = AgentContext(question="q")
        ctx.set_output("shared", 0)
        stop = False

        def writer():
            while not stop:
                ctx.set_output("shared", ctx.get_output("shared", 0) + 1)

        def reader():
            while not stop:
                _ = ctx.get_output("shared")

        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as ex:
            futures = [ex.submit(writer) for _ in range(2)] + [ex.submit(reader) for _ in range(2)]
            time.sleep(0.1)
            stop = True
            concurrent.futures.wait(futures)


class TestAgentStep:
    def test_agent_step_creation(self):
        step = AgentStep(name="knowledge", duration_ms=150, summary="搜索完成")
        assert step.name == "knowledge"
        assert step.duration_ms == 150
        assert step.summary == "搜索完成"

    def test_add_trace(self):
        ctx = AgentContext(question="q")
        trace = AgentTrace(agent="knowledge", start_time="0", end_time="1")
        ctx.add_trace(trace)
        assert len(ctx.traces) == 1
        assert ctx.traces[0].agent == "knowledge"


class TestTaskIdIsolation:
    def test_set_output_by_task_id(self):
        """同一 key 被不同 task_id 写入，get_output 自动合并 list 值"""
        ctx = AgentContext(question="q")
        ctx.current_task_id = "t1"
        ctx.set_output("evidence", [Evidence(statement="A", source="f1", evidence_type="t")])
        ctx.current_task_id = "t2"
        ctx.set_output("evidence", [Evidence(statement="B", source="f2", evidence_type="t")])

        merged = ctx.get_output("evidence")
        assert len(merged) == 2
        # 验证按 task_id 隔离存储
        assert ctx.outputs["evidence"]["t1"].producer == ""
        assert ctx.outputs["evidence"]["t2"].producer == ""

    def test_get_output_entry_with_task_id(self):
        ctx = AgentContext(question="q")
        ctx.current_task_id = "t1"
        ctx.set_output("answer", "from_t1", producer="gen")
        ctx.current_task_id = "t2"
        ctx.set_output("answer", "from_t2", producer="gen")

        # 不指定 task_id → 返回最后一个
        assert ctx.get_output("answer") == "from_t2"
        # 指定 task_id → 返回指定 task 的
        assert ctx.get_output_entry("answer", task_id="t1").producer == "gen"
        assert ctx.get_output_entry("answer", task_id="t2").value == "from_t2"

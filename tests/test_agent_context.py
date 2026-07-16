"""AgentContext 单元测试 — 写保护"""
import pytest

from app.core.agent_context import AgentContext, AgentStep
from app.models.data_types import Evidence, AnalysisResult, CriticResult, AgentTrace


class TestWriteProtection:
    def test_set_evidence_first_time_ok(self):
        ctx = AgentContext(question="q")
        evidence = [Evidence(statement="s", source="f", evidence_type="fact")]
        ctx.set_evidence(evidence)
        assert ctx.evidence == evidence

    def test_set_evidence_double_write_raises(self):
        ctx = AgentContext(question="q")
        ctx.set_evidence([Evidence(statement="s", source="f", evidence_type="fact")])
        with pytest.raises(RuntimeError, match="Evidence already exists"):
            ctx.set_evidence([Evidence(statement="s2", source="f2", evidence_type="fact")])

    def test_set_sources_first_time_ok(self):
        ctx = AgentContext(question="q")
        sources = [{"document_id": 1, "file_name": "f.xlsx"}]
        ctx.set_sources(sources)
        assert ctx.sources == sources

    def test_set_sources_double_write_raises(self):
        ctx = AgentContext(question="q")
        ctx.set_sources([{"document_id": 1, "file_name": "f.xlsx"}])
        with pytest.raises(RuntimeError, match="Sources already exists"):
            ctx.set_sources([{"document_id": 2, "file_name": "g.xlsx"}])

    def test_set_analysis_first_time_ok(self):
        ctx = AgentContext(question="q")
        analysis = AnalysisResult(calculations=[], findings=["f1"], conclusions=["c1"])
        ctx.set_analysis(analysis)
        assert ctx.analysis == analysis

    def test_set_analysis_double_write_raises(self):
        ctx = AgentContext(question="q")
        ctx.set_analysis(AnalysisResult())
        with pytest.raises(RuntimeError, match="Analysis already exists"):
            ctx.set_analysis(AnalysisResult(findings=["f2"]))

    def test_set_answer_first_time_ok(self):
        ctx = AgentContext(question="q")
        ctx.set_answer("第一次回答")
        assert ctx.answer == "第一次回答"

    def test_set_answer_double_write_raises(self):
        ctx = AgentContext(question="q")
        ctx.set_answer("第一次回答")
        with pytest.raises(RuntimeError, match="Answer already exists"):
            ctx.set_answer("第二次回答")

    def test_set_critique_first_time_ok(self):
        ctx = AgentContext(question="q")
        ctx.set_critique("不够好", need_retry=True, retry_target="knowledge")
        assert ctx.critique == "不够好"
        assert ctx.need_retry is True
        assert ctx.retry_target == "knowledge"

    def test_set_critique_double_write_raises(self):
        ctx = AgentContext(question="q")
        ctx.set_critique("第一次")
        with pytest.raises(RuntimeError, match="Critique already exists"):
            ctx.set_critique("第二次")

    def test_add_trace(self):
        ctx = AgentContext(question="q")
        trace = AgentTrace(agent="knowledge", start_time=0, end_time=1)
        ctx.add_trace(trace)
        assert len(ctx.traces) == 1
        assert ctx.traces[0].agent == "knowledge"


class TestResetFieldsClearedByOrchestrator:
    """验证 reset_for_retry 清空后可以重新写入（模拟 orchestrator 行为）"""

    def test_after_clearing_answer_can_rewrite(self):
        ctx = AgentContext(question="q")
        ctx.set_answer("old")
        # 模拟 orchestrator reset
        ctx.answer = ""
        ctx.set_answer("new")
        assert ctx.answer == "new"

    def test_after_clearing_critique_can_rewrite(self):
        ctx = AgentContext(question="q")
        ctx.set_critique("old", need_retry=True, retry_target="knowledge")
        # 模拟 orchestrator reset
        ctx.critique = ""
        ctx.need_retry = False
        ctx.retry_target = "all"
        ctx.set_critique("new", need_retry=False)
        assert ctx.critique == "new"

    def test_after_clearing_evidence_can_rewrite(self):
        ctx = AgentContext(question="q")
        ctx.set_evidence([Evidence(statement="s", source="f", evidence_type="fact")])
        # 模拟 orchestrator reset
        ctx.evidence = []
        ctx.set_evidence([Evidence(statement="s2", source="f2", evidence_type="fact")])
        assert len(ctx.evidence) == 1

    def test_after_clearing_analysis_can_rewrite(self):
        ctx = AgentContext(question="q")
        ctx.set_analysis(AnalysisResult(findings=["f1"]))
        # 模拟 orchestrator reset
        ctx.analysis = None
        ctx.set_analysis(AnalysisResult(findings=["f2"]))
        assert ctx.analysis.findings == ["f2"]


class TestAgentStep:
    def test_agent_step_creation(self):
        step = AgentStep(name="knowledge", duration_ms=150, summary="搜索完成")
        assert step.name == "knowledge"
        assert step.duration_ms == 150
        assert step.summary == "搜索完成"

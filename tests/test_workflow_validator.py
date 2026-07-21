"""WorkflowValidator 单元测试 — DAG 结构校验 + 分层计算"""
import pytest

from app.core.workflow_validator import WorkflowValidator
from app.models.task_graph import TaskGraph, TaskNode


class TestValidateStructure:
    def setup_method(self):
        self.validator = WorkflowValidator()

    def test_valid_dag(self):
        plan = TaskGraph(
            goal="分析",
            tasks=[
                TaskNode(id="t1", agent="knowledge", objective="检索"),
                TaskNode(id="t2", agent="analysis", objective="分析", depends_on=["t1"]),
            ],
        )
        errors = self.validator.validate_structure(plan)
        assert errors == []

    def test_empty_plan(self):
        plan = TaskGraph(goal="空", tasks=[])
        errors = self.validator.validate_structure(plan)
        assert any("不能为空" in e for e in errors)

    def test_missing_dependency(self):
        plan = TaskGraph(
            goal="test",
            tasks=[TaskNode(id="t1", agent="knowledge", objective="q", depends_on=["nonexistent"])],
        )
        errors = self.validator.validate_structure(plan)
        assert any("依赖不存在" in e for e in errors)

    def test_cycle_detection(self):
        plan = TaskGraph(
            goal="test",
            tasks=[
                TaskNode(id="t1", agent="a", objective="a", depends_on=["t2"]),
                TaskNode(id="t2", agent="b", objective="b", depends_on=["t1"]),
            ],
        )
        errors = self.validator.validate_structure(plan)
        assert any("循环依赖" in e for e in errors)


class TestGetLayers:
    def setup_method(self):
        self.validator = WorkflowValidator()

    def test_single_layer(self):
        plan = TaskGraph(
            goal="test",
            tasks=[
                TaskNode(id="t1", agent="knowledge", objective="k1"),
                TaskNode(id="t2", agent="knowledge", objective="k2"),
            ],
        )
        layers = self.validator.get_layers(plan)
        assert layers == {"t1": 0, "t2": 0}

    def test_multi_layer_chain(self):
        plan = TaskGraph(
            goal="test",
            tasks=[
                TaskNode(id="t1", agent="knowledge", objective="k1"),
                TaskNode(id="t2", agent="analysis", objective="a1", depends_on=["t1"]),
                TaskNode(id="t3", agent="generator", objective="g1", depends_on=["t2"]),
            ],
        )
        layers = self.validator.get_layers(plan)
        assert layers == {"t1": 0, "t2": 1, "t3": 2}

    def test_parallel_branches(self):
        plan = TaskGraph(
            goal="test",
            tasks=[
                TaskNode(id="t1", agent="knowledge", objective="k1"),
                TaskNode(id="t2", agent="knowledge", objective="k2"),
                TaskNode(id="t3", agent="analysis", objective="a1", depends_on=["t1", "t2"]),
            ],
        )
        layers = self.validator.get_layers(plan)
        assert layers["t1"] == 0
        assert layers["t2"] == 0
        assert layers["t3"] == 1


class TestDAGDataFlowValidator:
    def setup_method(self):
        from app.core.workflow_validator import DAGDataFlowValidator
        from app.core.agent_registry import AgentRegistry
        from app.models.capability import AgentCapability
        self.validator = DAGDataFlowValidator()
        self.registry = AgentRegistry()
        self.registry.register(AgentCapability(
            name="knowledge",
            outputs={"evidence": list, "sources": list},
        ))
        self.registry.register(AgentCapability(
            name="analysis",
            outputs={"analysis": object},
        ))
        self.registry.register(AgentCapability(
            name="generator",
            outputs={"answer": str},
        ))

    def test_valid_port_bindings(self):
        plan = TaskGraph(
            goal="test",
            tasks=[
                TaskNode(id="t1", agent="knowledge", objective="检索"),
                TaskNode(id="t2", agent="analysis", objective="分析",
                         depends_on=["t1"], port_bindings={"documents": "t1.evidence"}),
            ],
        )
        errors = self.validator.validate_port_bindings(plan, self.registry)
        assert errors == []

    def test_missing_task_id_prefix(self):
        plan = TaskGraph(
            goal="test",
            tasks=[
                TaskNode(id="t1", agent="knowledge", objective="检索"),
                TaskNode(id="t2", agent="analysis", objective="分析",
                         depends_on=["t1"], port_bindings={"documents": "evidence"}),
            ],
        )
        errors = self.validator.validate_port_bindings(plan, self.registry)
        assert any("缺少 task_id." in e for e in errors)

    def test_non_existent_source_task(self):
        plan = TaskGraph(
            goal="test",
            tasks=[
                TaskNode(id="t1", agent="generator", objective="生成",
                         port_bindings={"evidence": "nonexistent.evidence"}),
            ],
        )
        errors = self.validator.validate_port_bindings(plan, self.registry)
        assert any("不存在" in e for e in errors)

    def test_not_an_ancestor(self):
        plan = TaskGraph(
            goal="test",
            tasks=[
                TaskNode(id="t1", agent="knowledge", objective="检索"),
                TaskNode(id="t2", agent="generator", objective="生成",
                         port_bindings={"documents": "t1.evidence"}),
            ],
        )
        errors = self.validator.validate_port_bindings(plan, self.registry)
        assert any("非上游" in e for e in errors)

    def test_missing_output_key(self):
        plan = TaskGraph(
            goal="test",
            tasks=[
                TaskNode(id="t1", agent="knowledge", objective="检索"),
                TaskNode(id="t2", agent="generator", objective="生成",
                         depends_on=["t1"], port_bindings={"data": "t1.nonexistent"}),
            ],
        )
        errors = self.validator.validate_port_bindings(plan, self.registry)
        assert any("无 output_key" in e for e in errors)

    def test_empty_port_bindings(self):
        plan = TaskGraph(
            goal="test",
            tasks=[
                TaskNode(id="t1", agent="knowledge", objective="检索"),
                TaskNode(id="t2", agent="generator", objective="生成", depends_on=["t1"]),
            ],
        )
        errors = self.validator.validate_port_bindings(plan, self.registry)
        assert errors == []

    def test_transitive_ancestor(self):
        plan = TaskGraph(
            goal="test",
            tasks=[
                TaskNode(id="t1", agent="knowledge", objective="检索"),
                TaskNode(id="t2", agent="analysis", objective="分析", depends_on=["t1"]),
                TaskNode(id="t3", agent="generator", objective="生成",
                         depends_on=["t2"], port_bindings={"data": "t1.evidence"}),
            ],
        )
        errors = self.validator.validate_port_bindings(plan, self.registry)
        assert errors == []

    def test_multiple_port_bindings(self):
        plan = TaskGraph(
            goal="test",
            tasks=[
                TaskNode(id="t1", agent="knowledge", objective="检索"),
                TaskNode(id="t2", agent="analysis", objective="分析", depends_on=["t1"]),
                TaskNode(id="t3", agent="generator", objective="生成",
                         depends_on=["t1", "t2"],
                         port_bindings={"evidence": "t1.evidence", "analysis": "t2.analysis"}),
            ],
        )
        errors = self.validator.validate_port_bindings(plan, self.registry)
        assert errors == []

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

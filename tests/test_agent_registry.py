"""AgentRegistry 单元测试 — 注册 + Agent 能力校验"""
import pytest

from app.core.agent_registry import AgentRegistry
from app.models.capability import AgentCapability
from app.models.task_graph import TaskGraph, TaskNode


def _make_cap(name: str, tools=None, writes_to=None, requires=None) -> AgentCapability:
    return AgentCapability(
        name=name,
        description=f"{name} agent",
        tools=tools or [],
        inputs=requires or [],
        outputs={k: str for k in (writes_to or [])},
    )


class TestRegisterAndGet:
    def test_register_and_get(self):
        reg = AgentRegistry()
        cap = _make_cap("knowledge", tools=["search"])
        reg.register(cap)
        assert reg.get("knowledge") == cap

    def test_get_nonexistent_returns_none(self):
        reg = AgentRegistry()
        assert reg.get("unknown") is None

    def test_get_agent_instance(self):
        reg = AgentRegistry()
        cap = _make_cap("knowledge")
        instance = object()
        reg.register(cap, instance=instance)
        assert reg.get_agent("knowledge") is instance

    def test_get_agent_no_instance_returns_none(self):
        reg = AgentRegistry()
        cap = _make_cap("knowledge")
        reg.register(cap)
        assert reg.get_agent("knowledge") is None

    def test_all_capabilities(self):
        reg = AgentRegistry()
        reg.register(_make_cap("knowledge"))
        reg.register(_make_cap("analysis"))
        caps = reg.all_capabilities()
        assert len(caps) == 2
        assert {c.name for c in caps} == {"knowledge", "analysis"}

    def test_valid_names(self):
        reg = AgentRegistry()
        reg.register(_make_cap("knowledge"))
        reg.register(_make_cap("analysis"))
        assert reg.valid_names() == {"knowledge", "analysis"}


class TestFindByTool:
    def test_find_by_tool(self):
        reg = AgentRegistry()
        reg.register(_make_cap("knowledge", tools=["search", "list"]))
        reg.register(_make_cap("analysis", tools=["calculate"]))
        result = reg.find_by_tool("search")
        assert len(result) == 1
        assert result[0].name == "knowledge"

    def test_find_by_tool_none_match(self):
        reg = AgentRegistry()
        reg.register(_make_cap("knowledge", tools=["search"]))
        assert reg.find_by_tool("calculate") == []


class TestFindByWrites:
    def test_find_by_writes(self):
        reg = AgentRegistry()
        reg.register(_make_cap("knowledge", writes_to=["evidence", "sources"]))
        reg.register(_make_cap("analysis", writes_to=["analysis"]))
        result = reg.find_by_writes("evidence")
        assert len(result) == 1
        assert result[0].name == "knowledge"

    def test_find_by_writes_none_match(self):
        reg = AgentRegistry()
        reg.register(_make_cap("knowledge", writes_to=["evidence"]))
        assert reg.find_by_writes("answer") == []


class TestValidateCapabilities:
    def test_valid_dag(self):
        reg = AgentRegistry()
        reg.register(_make_cap("knowledge", writes_to=["evidence"]))
        reg.register(_make_cap("analysis", writes_to=["analysis"], requires=["evidence"]))

        plan = TaskGraph(
            goal="分析",
            tasks=[
                TaskNode(id="t1", agent="knowledge", objective="检索"),
                TaskNode(id="t2", agent="analysis", objective="分析", depends_on=["t1"]),
            ],
        )
        layers = {"t1": 0, "t2": 1}
        errors = reg.validate_capabilities(plan, layers)
        assert errors == []

    def test_unregistered_agent(self):
        reg = AgentRegistry()
        plan = TaskGraph(
            goal="test",
            tasks=[TaskNode(id="t1", agent="unknown", objective="??")],
        )
        layers = {"t1": 0}
        errors = reg.validate_capabilities(plan, layers)
        assert any("未注册" in e for e in errors)

    def test_missing_input(self):
        reg = AgentRegistry()
        reg.register(_make_cap("analysis", requires=["evidence"]))
        plan = TaskGraph(
            goal="test",
            tasks=[TaskNode(id="t1", agent="analysis", objective="分析")],
        )
        layers = {"t1": 0}
        errors = reg.validate_capabilities(plan, layers)
        assert any("缺少输入" in e for e in errors)

    def test_input_satisfied(self):
        reg = AgentRegistry()
        reg.register(_make_cap("knowledge", writes_to=["evidence"]))
        reg.register(_make_cap("analysis", writes_to=["analysis"], requires=["evidence"]))

        plan = TaskGraph(
            goal="test",
            tasks=[
                TaskNode(id="t1", agent="knowledge", objective="检索"),
                TaskNode(id="t2", agent="analysis", objective="分析", depends_on=["t1"]),
            ],
        )
        layers = {"t1": 0, "t2": 1}
        errors = reg.validate_capabilities(plan, layers)
        assert not any("缺少输入" in e for e in errors)

    def test_output_conflict_same_layer_allowed(self):
        """同层输出冲突不再报错，因为 set_output 已按 task_id 隔离"""
        reg = AgentRegistry()
        reg.register(_make_cap("knowledge", writes_to=["evidence"]))
        reg.register(_make_cap("another", writes_to=["evidence"]))
        plan = TaskGraph(
            goal="test",
            tasks=[
                TaskNode(id="t1", agent="knowledge", objective="k"),
                TaskNode(id="t2", agent="another", objective="a"),
            ],
        )
        layers = {"t1": 0, "t2": 0}
        errors = reg.validate_capabilities(plan, layers)
        assert not any("输出冲突" in e for e in errors)

    def test_output_conflict_different_layers_ok(self):
        reg = AgentRegistry()
        reg.register(_make_cap("knowledge", writes_to=["evidence"]))
        reg.register(_make_cap("another", writes_to=["evidence"]))
        plan = TaskGraph(
            goal="test",
            tasks=[
                TaskNode(id="t1", agent="knowledge", objective="k"),
                TaskNode(id="t2", agent="another", objective="a", depends_on=["t1"]),
            ],
        )
        layers = {"t1": 0, "t2": 1}
        errors = reg.validate_capabilities(plan, layers)
        assert not any("输出冲突" in e for e in errors)


class TestFormatForPrompt:
    def test_format_for_prompt(self):
        reg = AgentRegistry()
        reg.register(_make_cap("knowledge", tools=["search", "list"]))
        reg.register(_make_cap("analysis", tools=["calculate"]))
        text = reg.format_for_prompt()
        assert "knowledge" in text
        assert "search" in text
        assert "analysis" in text

    def test_format_for_prompt_no_tools(self):
        reg = AgentRegistry()
        reg.register(_make_cap("coordinator", tools=[]))
        text = reg.format_for_prompt()
        assert "无" in text

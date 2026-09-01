import pytest

from app.core.agent_context import AgentContext
from app.core.infra.run_state_store import RunStateStore
from app.models.data_types import DocumentBundle, DocumentChunk
from app.models.task_graph import TaskGraph, TaskNode, TaskStatus


class TestRunStateSerialization:
    def test_nested_dataclasses_keep_their_runtime_types(self):
        value = DocumentBundle(chunks=[DocumentChunk(source="账.xlsx", content="品牌: 万代")])

        restored = RunStateStore._decode_value(RunStateStore._encode_value(value))

        assert isinstance(restored, DocumentBundle)
        assert isinstance(restored.chunks[0], DocumentChunk)
        assert restored.chunks[0].source == "账.xlsx"

    def test_task_graph_round_trip_preserves_execution_metadata(self):
        plan = TaskGraph(
            goal="统计各品牌花费",
            goal_outputs=["answer"],
            tasks=[TaskNode(
                id="task1", agent="retrieval", objective="读取账单",
                port_bindings={"query": "task0.query"}, status=TaskStatus.COMPLETED,
                duration_ms=2700, summary="命中账.xlsx", tools_used=["search_documents"],
            )],
        )

        restored = RunStateStore._deserialize_plan(RunStateStore._serialize_plan(plan))

        assert restored.goal == plan.goal
        assert restored.tasks[0].status == TaskStatus.COMPLETED
        assert restored.tasks[0].port_bindings == {"query": "task0.query"}
        assert restored.tasks[0].duration_ms == 2700

    def test_outputs_are_keyed_by_producer_task_for_resume(self):
        context = AgentContext(question="测试")
        context.current_task_id = "task1"
        context.set_output("document_bundle", DocumentBundle(chunks=[]), producer="retrieval")

        restored = RunStateStore._restore_outputs(RunStateStore._serialize_outputs(context))

        assert isinstance(restored["document_bundle"]["task1"].value, DocumentBundle)
        assert restored["document_bundle"]["task1"].producer == "retrieval"

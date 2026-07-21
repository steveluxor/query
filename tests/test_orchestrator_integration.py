"""Orchestrator 集成测试 — 覆盖 DAG 执行、MCP Session 生命周期、记忆管理"""

import asyncio
from unittest.mock import AsyncMock, MagicMock

import anyio
import pytest

from app.core.agent_context import AgentContext
from app.core.agent_orchestrator import AgentOrchestrator
from app.models.data_types import Evidence, AnalysisResult, Calculation, DocumentBundle, RetrievalReport
from app.models.task_graph import TaskGraph, TaskNode


# ==================== Fixtures ====================

@pytest.fixture
def mock_mcp_client():
    client = AsyncMock()
    client.create_session = AsyncMock(return_value="test-session-uuid")
    client.cleanup_session = AsyncMock()
    client.call_tool = AsyncMock(return_value="ok")
    return client


@pytest.fixture
def mock_agent_memory():
    mem = MagicMock()
    mem.has_session = MagicMock(return_value=False)
    mem.restore_session = MagicMock()
    mem.format_context = MagicMock(return_value="")
    mem.update = MagicMock()
    mem.update_preferences = MagicMock()
    mem.to_dict = MagicMock(return_value=None)
    mem.rebuild_from_history = MagicMock()
    return mem


@pytest.fixture
def mock_redis_store():
    store = AsyncMock()
    store.safe_get_memory = AsyncMock(return_value=None)
    store.safe_get_history = AsyncMock(return_value=[])
    return store


@pytest.fixture
def mock_rag_engine():
    engine = MagicMock()
    engine.vector_store = MagicMock()
    engine.vector_store.get_document_names = MagicMock(return_value={})
    engine.llm = MagicMock()
    return engine


@pytest.fixture
def orchestrator(mock_rag_engine, mock_agent_memory, mock_redis_store, mock_mcp_client):
    return AgentOrchestrator(mock_rag_engine, mock_agent_memory, mock_redis_store, mock_mcp_client)


def _make_execute_mock(side_effect=None):
    """创建 execute mock — 直接执行 side_effect 并返回 AgentResult"""
    async def _execute(ctx, task_id="", **kwargs):
        from app.models.data_types import AgentResult
        if side_effect:
            return await side_effect(ctx, task_id=task_id, **kwargs)
        return AgentResult()
    return AsyncMock(side_effect=_execute)


# ==================== DAG 执行 ====================

class TestDAGExecution:

    @pytest.mark.anyio
    async def test_retrieval_extractor_generator(self, orchestrator, mock_mcp_client):
        """Retrieval → Extractor → Generator 全链路"""
        context = AgentContext(question="什么是RAG", session_id="s1", document_ids=[1])

        plan = TaskGraph(
            goal="回答问题",
            goal_outputs=["answer"],
            tasks=[
                TaskNode(id="task1", agent="retrieval", objective="检索", depends_on=[]),
                TaskNode(id="task2", agent="extractor", objective="提取", depends_on=["task1"]),
                TaskNode(id="task3", agent="generator", objective="回答", depends_on=["task2"]),
            ],
        )
        orchestrator._plan = MagicMock(return_value=plan)

        async def retrieval_side_effect(ctx, task_id="", **kw):
            ctx.set_output("document_bundle", DocumentBundle(chunks=[]), producer="retrieval")
            ctx.set_output("retrieval_report", RetrievalReport(), producer="retrieval")
            from app.models.data_types import AgentResult
            return AgentResult()
        async def extractor_side_effect(ctx, task_id="", **kw):
            ctx.set_output("knowledge_objects", [], producer="extractor")
            ctx.set_output("evidence", [Evidence(statement="RAG是检索增强生成", source="doc.pdf", evidence_type="text")], producer="extractor")
            ctx.set_output("sources", [{"file_name": "doc.pdf", "content": "RAG是..."}], producer="extractor")
            from app.models.data_types import AgentResult
            return AgentResult()

        orchestrator.registry.get_agent("retrieval").execute = _make_execute_mock(retrieval_side_effect)
        orchestrator.registry.get_agent("extractor").execute = _make_execute_mock(extractor_side_effect)
        orchestrator.registry.get_agent("generator").execute = AsyncMock(side_effect=lambda ctx, **kw: (
            ctx.set_output("answer", "RAG是检索增强生成技术", producer="generator"),
            type("AgentResult", (), {"outputs": {}, "actions": []})())[1])

        result = await orchestrator.run(context)

        mock_mcp_client.create_session.assert_called_once()
        mock_mcp_client.cleanup_session.assert_called_once_with("test-session-uuid")
        assert result.mcp_session_id == "test-session-uuid"
        assert result.get_output("answer") == "RAG是检索增强生成技术"

    @pytest.mark.anyio
    async def test_two_steps(self, orchestrator, mock_mcp_client):
        """两步 DAG: task1 → task2，验证 MCP kwargs 传递"""
        context = AgentContext(question="对比两个实验", session_id="s1", document_ids=[1, 2])

        plan = TaskGraph(
            goal="对比两个实验",
            tasks=[
                TaskNode(id="task1", agent="retrieval", objective="获取数据A", depends_on=[]),
                TaskNode(id="task2", agent="retrieval", objective="获取数据B", depends_on=["task1"]),
            ],
        )
        orchestrator._plan = MagicMock(return_value=plan)

        executed_tasks = []
        mcp_clients_received = []

        async def retrieval_side_effect(ctx, task_id="", **kw):
            executed_tasks.append(task_id)
            mcp_clients_received.append(kw.get("mcp_client"))
            from app.models.data_types import AgentResult
            return AgentResult()

        orchestrator.registry.get_agent("retrieval").execute = _make_execute_mock(retrieval_side_effect)

        result = await orchestrator.run(context)

        assert executed_tasks == ["task1", "task2"]
        for mc in mcp_clients_received:
            assert mc is mock_mcp_client
        mock_mcp_client.create_session.assert_called_once()
        mock_mcp_client.cleanup_session.assert_called_once()

    @pytest.mark.anyio
    async def test_dag_dependency_order(self, orchestrator, mock_mcp_client):
        """DAG: task3 依赖 task1+task2，验证拓扑排序"""
        context = AgentContext(question="综合分析", session_id="s1", document_ids=[1])

        plan = TaskGraph(
            goal="综合分析",
            tasks=[
                TaskNode(id="task3", agent="generator", objective="综合", depends_on=["task1", "task2"]),
                TaskNode(id="task1", agent="retrieval", objective="数据A", depends_on=[]),
                TaskNode(id="task2", agent="retrieval", objective="数据B", depends_on=[]),
            ],
        )
        orchestrator._plan = MagicMock(return_value=plan)

        execution_order = []
        async def retrieval_side_effect(ctx, task_id="", **kw):
            execution_order.append(task_id)
            from app.models.data_types import AgentResult
            return AgentResult()
        async def generator_side_effect(ctx, task_id="", **kw):
            execution_order.append(task_id)
            ctx.set_output("answer", "综合结果", producer="generator")
            from app.models.data_types import AgentResult
            return AgentResult()

        orchestrator.registry.get_agent("retrieval").execute = _make_execute_mock(retrieval_side_effect)
        orchestrator.registry.get_agent("generator").execute = _make_execute_mock(generator_side_effect)

        result = await orchestrator.run(context)

        idx1 = execution_order.index("task1")
        idx2 = execution_order.index("task2")
        idx3 = execution_order.index("task3")
        assert idx1 < idx3
        assert idx2 < idx3



# ==================== MCP Session 生命周期 ====================

class TestMCPSessionLifecycle:

    @pytest.mark.anyio
    async def test_session_cleanup_on_success(self, orchestrator, mock_mcp_client):
        """正常流程: session 在 finally 中被清理"""
        context = AgentContext(question="测试", session_id="s1")
        plan = TaskGraph(
            goal="测试",
            goal_outputs=["answer"],
            tasks=[TaskNode(id="task1", agent="chat", objective="测试", depends_on=[])],
        )
        orchestrator._plan = MagicMock(return_value=plan)
        orchestrator.registry.get_agent("chat").execute = AsyncMock(side_effect=lambda ctx, **kw: (
            ctx.set_output("answer", "答案", producer="chat"),
            type("AgentResult", (), {"outputs": {}, "actions": []})())[1])

        await orchestrator.run(context)

        mock_mcp_client.create_session.assert_called_once()
        mock_mcp_client.cleanup_session.assert_called_once_with("test-session-uuid")

    @pytest.mark.anyio
    async def test_session_cleanup_on_agent_error(self, orchestrator, mock_mcp_client):
        """Agent 异常: session 仍然被清理"""
        context = AgentContext(question="会出错的问题", session_id="s1")
        plan = TaskGraph(
            goal="测试",
            goal_outputs=["answer"],
            tasks=[TaskNode(id="task1", agent="retrieval", objective="出错", depends_on=[])],
        )
        orchestrator._plan = MagicMock(return_value=plan)

        async def retrieval_fail(ctx, **kw):
            raise RuntimeError("搜索失败")
        orchestrator.registry.get_agent("retrieval").execute = _make_execute_mock(retrieval_fail)

        with pytest.raises(RuntimeError, match="搜索失败"):
            await orchestrator.run(context)

        mock_mcp_client.create_session.assert_called_once()
        mock_mcp_client.cleanup_session.assert_called_once_with("test-session-uuid")

    @pytest.mark.anyio
    async def test_session_id_passed_to_set_document_ids(self, orchestrator, mock_mcp_client):
        """验证 set_document_ids 使用正确的 session_id"""
        context = AgentContext(question="测试", session_id="s1", document_ids=[10, 20, 30])
        plan = TaskGraph(
            goal="测试",
            goal_outputs=["answer"],
            tasks=[TaskNode(id="task1", agent="chat", objective="测试", depends_on=[])],
        )
        orchestrator._plan = MagicMock(return_value=plan)
        orchestrator.registry.get_agent("chat").execute = AsyncMock(side_effect=lambda ctx, **kw: (
            ctx.set_output("answer", "答案", producer="chat"),
            type("AgentResult", (), {"outputs": {}, "actions": []})())[1])

        await orchestrator.run(context)

        call_args = mock_mcp_client.call_tool.call_args
        assert call_args[1]["session_id"] == "test-session-uuid"
        assert call_args[0] == ("set_document_ids", {"ids": [10, 20, 30]})

    @pytest.mark.anyio
    async def test_no_document_ids_skips_set_document_ids(self, orchestrator, mock_mcp_client):
        """无 document_ids 时跳过 set_document_ids"""
        context = AgentContext(question="测试", session_id="s1", document_ids=None)
        plan = TaskGraph(
            goal="测试",
            goal_outputs=["answer"],
            tasks=[TaskNode(id="task1", agent="chat", objective="测试", depends_on=[])],
        )
        orchestrator._plan = MagicMock(return_value=plan)
        orchestrator.registry.get_agent("chat").execute = AsyncMock(side_effect=lambda ctx, **kw: (
            ctx.set_output("answer", "答案", producer="chat"),
            type("AgentResult", (), {"outputs": {}, "actions": []})())[1])

        await orchestrator.run(context)

        for call in mock_mcp_client.call_tool.call_args_list:
            assert call[0][0] != "set_document_ids"


# ==================== BaseAgent kwargs 转发 ====================

class TestBaseAgentKwargsForwarding:

    @pytest.mark.anyio
    async def test_execute_forwards_mcp_kwargs(self, orchestrator):
        """execute(mcp_client=..., mcp_session_id=...) 转发到 run()"""
        agent = orchestrator.registry.get_agent("retrieval")

        received = {}

        async def mock_run(ctx, mcp_client=None, mcp_session_id=""):
            received["mcp_client"] = mcp_client
            received["mcp_session_id"] = mcp_session_id
            return ctx
        agent.run = mock_run

        mock_client = MagicMock()
        ctx = AgentContext(question="test")
        await agent.execute(ctx, task_id="task1", mcp_client=mock_client, mcp_session_id="uuid-123")

        assert received["mcp_client"] is mock_client
        assert received["mcp_session_id"] == "uuid-123"

    @pytest.mark.anyio
    async def test_execute_no_extra_kwargs(self, orchestrator):
        """execute() 不传额外参数时 run() 正常"""
        agent = orchestrator.registry.get_agent("retrieval")

        received = {}

        async def mock_run(ctx, mcp_client=None, mcp_session_id=""):
            received["mcp_client"] = mcp_client
            received["mcp_session_id"] = mcp_session_id
            return ctx
        agent.run = mock_run

        ctx = AgentContext(question="test")
        await agent.execute(ctx)

        assert received["mcp_client"] is None
        assert received["mcp_session_id"] == ""


# ==================== 记忆管理 ====================

class TestMemoryIntegration:

    @pytest.mark.anyio
    async def test_memory_restored_on_start(self, orchestrator, mock_agent_memory, mock_redis_store):
        """请求开始时恢复记忆"""
        context = AgentContext(question="测试", session_id="user-123")
        plan = TaskGraph(
            goal="测试",
            goal_outputs=["answer"],
            tasks=[TaskNode(id="task1", agent="chat", objective="测试", depends_on=[])],
        )
        orchestrator._plan = MagicMock(return_value=plan)
        orchestrator.registry.get_agent("chat").execute = AsyncMock(side_effect=lambda ctx, **kw: (
            ctx.set_output("answer", "答案", producer="chat"),
            type("AgentResult", (), {"outputs": {}, "actions": []})())[1])

        mock_redis_store.safe_get_history = AsyncMock(return_value=[{"question": "之前的问题", "answer": "之前的答案"}])

        await orchestrator.run(context)

        mock_agent_memory.has_session.assert_called_with("user-123")
        mock_agent_memory.format_context.assert_called_with("user-123")

    @pytest.mark.anyio
    async def test_memory_updated_on_end(self, orchestrator, mock_agent_memory):
        """请求结束时更新记忆"""
        context = AgentContext(question="测试", session_id="user-123")
        plan = TaskGraph(
            goal="测试",
            goal_outputs=["answer"],
            tasks=[TaskNode(id="task1", agent="chat", objective="测试", depends_on=[])],
        )
        orchestrator._plan = MagicMock(return_value=plan)
        orchestrator.registry.get_agent("chat").execute = AsyncMock(side_effect=lambda ctx, **kw: (
            ctx.set_output("answer", "答案", producer="chat"),
            type("AgentResult", (), {"outputs": {}, "actions": []})())[1])

        await orchestrator.run(context)

        mock_agent_memory.update.assert_called_once()
        call_args = mock_agent_memory.update.call_args
        assert call_args[0][0] == "user-123"
        assert call_args[0][1]["question"] == "测试"
        assert call_args[0][1]["answer"] == "答案"

    @pytest.mark.anyio
    async def test_no_memory_when_no_session(self, orchestrator, mock_agent_memory):
        """无 session_id 时跳过记忆操作"""
        context = AgentContext(question="测试", session_id=None)
        plan = TaskGraph(
            goal="测试",
            goal_outputs=["answer"],
            tasks=[TaskNode(id="task1", agent="chat", objective="测试", depends_on=[])],
        )
        orchestrator._plan = MagicMock(return_value=plan)
        orchestrator.registry.get_agent("chat").execute = AsyncMock(side_effect=lambda ctx, **kw: (
            ctx.set_output("answer", "答案", producer="chat"),
            type("AgentResult", (), {"outputs": {}, "actions": []})())[1])

        await orchestrator.run(context)

        mock_agent_memory.update.assert_not_called()


# ==================== Critic 重试集成测试 ====================

class TestCriticRetry:

    @pytest.mark.anyio
    async def test_critic_retry_by_agent_name(self, orchestrator, mock_mcp_client):
        """Critic 返回 retry_target='retrieval' → 仅 retrieval 及其下游被重试"""
        context = AgentContext(question="测试", session_id="s1")
        plan = TaskGraph(
            goal="测试",
            goal_outputs=["answer"],
            tasks=[
                TaskNode(id="task1", agent="retrieval", objective="检索", depends_on=[]),
                TaskNode(id="task2", agent="extractor", objective="提取", depends_on=["task1"]),
                TaskNode(id="task3", agent="generator", objective="生成", depends_on=["task2"]),
                TaskNode(id="task4", agent="critic", objective="审核", depends_on=["task3"]),
            ],
        )
        orchestrator._plan = MagicMock(return_value=plan)

        # 统计各 agent 执行次数
        call_counts = {"retrieval": 0, "extractor": 0, "generator": 0, "critic": 0}

        from app.models.data_types import AgentResult

        async def retrieval_side_effect(ctx, task_id="", **kw):
            call_counts["retrieval"] += 1
            ctx.set_output("document_bundle", MagicMock(chunks=[]), producer="retrieval")
            ctx.set_output("retrieval_report", MagicMock(), producer="retrieval")
            return AgentResult()

        async def extractor_side_effect(ctx, task_id="", **kw):
            call_counts["extractor"] += 1
            ctx.set_output("knowledge_objects", [], producer="extractor")
            ctx.set_output("evidence", [], producer="extractor")
            ctx.set_output("sources", [], producer="extractor")
            return AgentResult()

        async def generator_side_effect(ctx, task_id="", **kw):
            call_counts["generator"] += 1
            ctx.set_output("answer", "答案", producer="generator")
            return AgentResult()

        async def critic_side_effect(ctx, task_id="", **kw):
            call_counts["critic"] += 1
            # 第一次执行时返回 need_retry=true + ControlAction
            if call_counts["critic"] == 1:
                ctx.set_output("need_retry", True, producer="critic")
                ctx.set_output("retry_target", "retrieval", producer="critic")
                from app.models.control import ControlAction
                return AgentResult(actions=[ControlAction(action_type="retry", target_task_id="retrieval")])
            else:
                ctx.set_output("need_retry", False, producer="critic")
                ctx.set_output("retry_target", "all", producer="critic")
                return AgentResult()

        orchestrator.registry.get_agent("retrieval").execute = _make_execute_mock(retrieval_side_effect)
        orchestrator.registry.get_agent("extractor").execute = _make_execute_mock(extractor_side_effect)
        orchestrator.registry.get_agent("generator").execute = _make_execute_mock(generator_side_effect)
        orchestrator.registry.get_agent("critic").execute = _make_execute_mock(critic_side_effect)

        result = await orchestrator.run(context)

        # retrieval 和 extractor 应执行 2 次（首次 + 重试）
        assert call_counts["retrieval"] == 2, f"retrieval 应执行2次，实际{call_counts['retrieval']}"
        assert call_counts["extractor"] == 2, f"extractor 应执行2次，实际{call_counts['extractor']}"
        # generator 应执行 2 次（首次 + 重试）
        assert call_counts["generator"] == 2, f"generator 应执行2次，实际{call_counts['generator']}"
        # critic 仅首次执行（发起 retry 后自身状态被保留）
        assert call_counts["critic"] == 1, f"critic 应执行1次，实际{call_counts['critic']}"

        assert result.get_output("answer") == "答案"

    @pytest.mark.anyio
    async def test_critic_retry_generator_only(self, orchestrator, mock_mcp_client):
        """Critic 返回 retry_target='generator' → 只重试 generator"""
        context = AgentContext(question="测试", session_id="s1")
        plan = TaskGraph(
            goal="测试",
            goal_outputs=["answer"],
            tasks=[
                TaskNode(id="task1", agent="retrieval", objective="检索", depends_on=[]),
                TaskNode(id="task2", agent="extractor", objective="提取", depends_on=["task1"]),
                TaskNode(id="task3", agent="generator", objective="生成", depends_on=["task2"]),
                TaskNode(id="task4", agent="critic", objective="审核", depends_on=["task3"]),
            ],
        )
        orchestrator._plan = MagicMock(return_value=plan)

        call_counts = {"retrieval": 0, "extractor": 0, "generator": 0, "critic": 0}

        from app.models.data_types import AgentResult

        async def retrieval_side_effect(ctx, task_id="", **kw):
            call_counts["retrieval"] += 1
            ctx.set_output("document_bundle", MagicMock(chunks=[]), producer="retrieval")
            ctx.set_output("retrieval_report", MagicMock(), producer="retrieval")
            return AgentResult()

        async def extractor_side_effect(ctx, task_id="", **kw):
            call_counts["extractor"] += 1
            ctx.set_output("knowledge_objects", [], producer="extractor")
            ctx.set_output("evidence", [], producer="extractor")
            ctx.set_output("sources", [], producer="extractor")
            return AgentResult()

        async def generator_side_effect(ctx, task_id="", **kw):
            call_counts["generator"] += 1
            ctx.set_output("answer", "答案", producer="generator")
            return AgentResult()

        async def critic_side_effect(ctx, task_id="", **kw):
            call_counts["critic"] += 1
            if call_counts["critic"] == 1:
                ctx.set_output("need_retry", True, producer="critic")
                ctx.set_output("retry_target", "generator", producer="critic")
                from app.models.control import ControlAction
                return AgentResult(actions=[ControlAction(action_type="retry", target_task_id="generator")])
            else:
                ctx.set_output("need_retry", False, producer="critic")
                ctx.set_output("retry_target", "all", producer="critic")
                return AgentResult()

        orchestrator.registry.get_agent("retrieval").execute = _make_execute_mock(retrieval_side_effect)
        orchestrator.registry.get_agent("extractor").execute = _make_execute_mock(extractor_side_effect)
        orchestrator.registry.get_agent("generator").execute = _make_execute_mock(generator_side_effect)
        orchestrator.registry.get_agent("critic").execute = _make_execute_mock(critic_side_effect)

        result = await orchestrator.run(context)

        # 只有 generator 被重试，retrieval 和 extractor 不受影响
        assert call_counts["retrieval"] == 1, f"retrieval 应执行1次，实际{call_counts['retrieval']}"
        assert call_counts["extractor"] == 1, f"extractor 应执行1次，实际{call_counts['extractor']}"
        assert call_counts["generator"] == 2, f"generator 应执行2次，实际{call_counts['generator']}"
        # critic 仅首次执行（发起 retry 后自身状态被保留）
        assert call_counts["critic"] == 1, f"critic 应执行1次，实际{call_counts['critic']}"

        assert result.get_output("answer") == "答案"

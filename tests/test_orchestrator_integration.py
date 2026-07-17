"""Orchestrator 全链路集成测试 — 覆盖简单模式、规划模式、Critic 重试、MCP Session 生命周期"""

import asyncio
from unittest.mock import AsyncMock, MagicMock

import anyio
import pytest

from app.core.agent_context import AgentContext
from app.core.agent_orchestrator import AgentOrchestrator
from app.models.data_types import Evidence, AnalysisResult, Calculation
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
    """创建 execute mock — 直接执行 side_effect 并返回 context"""
    async def _execute(ctx, task_id="", **kwargs):
        if side_effect:
            return await side_effect(ctx, task_id=task_id, **kwargs)
        return ctx
    return AsyncMock(side_effect=_execute)


# ==================== 简单模式 ====================

class TestSimpleModeFullChain:

    @pytest.mark.anyio
    async def test_simple_knowledge_only(self, orchestrator, mock_mcp_client):
        """Knowledge → Generate，验证 MCP session + 答案生成"""
        context = AgentContext(question="什么是RAG", session_id="s1", document_ids=[1])

        orchestrator.coordinator.needs_plan = False
        orchestrator.coordinator.needs_analysis = False
        orchestrator.coordinator.needs_review = False
        orchestrator.coordinator.execute = AsyncMock(return_value=context)

        async def knowledge_side_effect(ctx, **kw):
            ctx.set_output("evidence", [Evidence(statement="RAG是检索增强生成", source="doc.pdf", evidence_type="text")], producer="knowledge")
            ctx.set_output("sources", [{"file_name": "doc.pdf", "content": "RAG是..."}], producer="knowledge")
            return ctx
        orchestrator.knowledge_agent.execute = _make_execute_mock(knowledge_side_effect)
        orchestrator.answer_generator.execute = AsyncMock(side_effect=lambda ctx, **kw: ctx.set_output("answer", "RAG是检索增强生成技术", producer="generator") or ctx)

        result = await orchestrator.run(context)

        # MCP session 生命周期
        mock_mcp_client.create_session.assert_called_once()
        mock_mcp_client.cleanup_session.assert_called_once_with("test-session-uuid")
        assert result.mcp_session_id == "test-session-uuid"

        # set_document_ids
        mock_mcp_client.call_tool.assert_called_with(
            "set_document_ids", {"ids": [1]}, session_id="test-session-uuid"
        )

        assert result.get_output("answer") == "RAG是检索增强生成技术"
        assert len(result.get_output("evidence") or []) == 1

    @pytest.mark.anyio
    async def test_simple_with_analysis(self, orchestrator, mock_mcp_client):
        """Knowledge → Analysis → Generate"""
        context = AgentContext(question="销售总额是多少", session_id="s1", document_ids=[1])

        orchestrator.coordinator.needs_plan = False
        orchestrator.coordinator.needs_analysis = True
        orchestrator.coordinator.needs_review = False
        orchestrator.coordinator.execute = AsyncMock(return_value=context)

        async def knowledge_side_effect(ctx, **kw):
            ctx.set_output("evidence", [Evidence(statement="销售额数据", source="sales.xlsx", evidence_type="table")], producer="knowledge")
            ctx.set_output("sources", [{"file_name": "sales.xlsx", "content": "..."}], producer="knowledge")
            return ctx
        orchestrator.knowledge_agent.execute = _make_execute_mock(knowledge_side_effect)

        async def analysis_side_effect(ctx, **kw):
            ctx.set_output("analysis", AnalysisResult(
                calculations=[Calculation(operation="sum", field="amount", result=5000, source="sales.xlsx")],
                findings=["总额5000"],
            ), producer="analysis")
            return ctx
        orchestrator.analysis_agent.execute = _make_execute_mock(analysis_side_effect)
        orchestrator.answer_generator.execute = AsyncMock(side_effect=lambda ctx, **kw: ctx.set_output("answer", "总额5000元", producer="generator") or ctx)

        result = await orchestrator.run(context)

        assert result.get_output("answer") == "总额5000元"
        assert result.get_output("analysis") is not None
        mock_mcp_client.create_session.assert_called_once()
        mock_mcp_client.cleanup_session.assert_called_once()


# ==================== 规划模式 ====================

class TestPlanningModeFullChain:

    @pytest.mark.anyio
    async def test_plan_mode_two_steps(self, orchestrator, mock_mcp_client):
        """两步规划: task1 → task2，验证 MCP kwargs 传递"""
        context = AgentContext(question="对比两个实验", session_id="s1", document_ids=[1, 2])

        orchestrator.coordinator.needs_plan = True
        orchestrator.coordinator.needs_analysis = False
        orchestrator.coordinator.needs_review = False
        orchestrator.coordinator.execute = AsyncMock(return_value=context)

        plan = TaskGraph(
            goal="对比两个实验",
            tasks=[
                TaskNode(id="task1", agent="knowledge", objective="获取实验A数据", depends_on=[]),
                TaskNode(id="task2", agent="knowledge", objective="获取实验B数据", depends_on=["task1"]),
            ],
        )

        executed_tasks = []
        mcp_clients_received = []

        async def knowledge_side_effect(ctx, task_id="", **kw):
            executed_tasks.append(task_id)
            mcp_clients_received.append(kw.get("mcp_client"))
            if task_id == "task1":
                ctx.set_output("evidence", [Evidence(statement="实验A结果", source="exp_a.xlsx", evidence_type="table")], producer="knowledge")
                ctx.set_output("sources", [{"file_name": "exp_a.xlsx", "content": "..."}], producer="knowledge")
            elif task_id == "task2":
                sources = list(ctx.get_output("sources") or [])
                sources.append({"file_name": "exp_b.xlsx", "content": "..."})
                ctx.set_output("sources", sources, producer="knowledge")
            return ctx

        orchestrator.knowledge_agent.execute = _make_execute_mock(knowledge_side_effect)
        orchestrator.answer_generator.execute = AsyncMock(side_effect=lambda ctx, **kw: ctx.set_output("answer", "对比结果", producer="generator") or ctx)
        orchestrator._plan = MagicMock(return_value=plan)

        result = await orchestrator.run(context)

        assert executed_tasks == ["task1", "task2"]
        for mc in mcp_clients_received:
            assert mc is mock_mcp_client
        mock_mcp_client.create_session.assert_called_once()
        mock_mcp_client.cleanup_session.assert_called_once()
        assert result.get_output("answer") == "对比结果"

    @pytest.mark.anyio
    async def test_plan_mode_requires_check(self, orchestrator, mock_mcp_client):
        """analysis agent 需要 evidence，无 evidence 时跳过"""
        context = AgentContext(question="计算并对比", session_id="s1", document_ids=[1])

        orchestrator.coordinator.needs_plan = True
        orchestrator.coordinator.needs_analysis = False
        orchestrator.coordinator.needs_review = False
        orchestrator.coordinator.execute = AsyncMock(return_value=context)

        plan = TaskGraph(
            goal="计算对比",
            tasks=[
                TaskNode(id="task1", agent="analysis", objective="计算总额", depends_on=[]),
            ],
        )

        executed_tasks = []

        async def analysis_side_effect(ctx, task_id="", **kw):
            executed_tasks.append(task_id)
            return ctx

        orchestrator.analysis_agent.execute = _make_execute_mock(analysis_side_effect)
        orchestrator.answer_generator.execute = AsyncMock(side_effect=lambda ctx, **kw: ctx.set_output("answer", "无答案", producer="generator") or ctx)
        orchestrator._plan = MagicMock(return_value=plan)

        result = await orchestrator.run(context)

        # analysis 需要 evidence 但为空，应跳过
        assert executed_tasks == []
        assert result.get_output("answer") == "无答案"

    @pytest.mark.anyio
    async def test_plan_mode_dag_dependency_order(self, orchestrator, mock_mcp_client):
        """DAG: task3 依赖 task1+task2，验证拓扑排序"""
        context = AgentContext(question="综合分析", session_id="s1", document_ids=[1])

        orchestrator.coordinator.needs_plan = True
        orchestrator.coordinator.needs_analysis = False
        orchestrator.coordinator.needs_review = False
        orchestrator.coordinator.execute = AsyncMock(return_value=context)

        plan = TaskGraph(
            goal="综合分析",
            tasks=[
                TaskNode(id="task3", agent="knowledge", objective="综合", depends_on=["task1", "task2"]),
                TaskNode(id="task1", agent="knowledge", objective="数据A", depends_on=[]),
                TaskNode(id="task2", agent="knowledge", objective="数据B", depends_on=[]),
            ],
        )

        execution_order = []

        async def knowledge_side_effect(ctx, task_id="", **kw):
            execution_order.append(task_id)
            if task_id in ("task1", "task2"):
                ctx.set_output("evidence", [Evidence(statement=f"{task_id}结果", source="x.xlsx", evidence_type="table")], producer="knowledge")
                ctx.set_output("sources", [{"file_name": "x.xlsx", "content": "..."}], producer="knowledge")
            return ctx

        orchestrator.knowledge_agent.execute = _make_execute_mock(knowledge_side_effect)
        orchestrator.answer_generator.execute = AsyncMock(side_effect=lambda ctx, **kw: ctx.set_output("answer", "综合结果", producer="generator") or ctx)
        orchestrator._plan = MagicMock(return_value=plan)

        result = await orchestrator.run(context)

        idx1 = execution_order.index("task1")
        idx2 = execution_order.index("task2")
        idx3 = execution_order.index("task3")
        assert idx1 < idx3
        assert idx2 < idx3


# ==================== Critic 重试 ====================

class TestCriticRetry:

    @pytest.mark.anyio
    async def test_critic_retry_knowledge(self, orchestrator, mock_mcp_client):
        """Critic 要求重跑 knowledge，验证 reset + 重跑"""
        context = AgentContext(question="测试问题", session_id="s1", document_ids=[1])

        orchestrator.coordinator.needs_plan = False
        orchestrator.coordinator.needs_analysis = False
        orchestrator.coordinator.needs_review = True
        orchestrator.coordinator.execute = AsyncMock(return_value=context)

        knowledge_call_count = 0

        async def knowledge_side_effect(ctx, **kw):
            nonlocal knowledge_call_count
            knowledge_call_count += 1
            if knowledge_call_count == 1:
                ctx.set_output("evidence", [Evidence(statement="不完整", source="x.xlsx", evidence_type="table")], producer="knowledge")
                ctx.set_output("sources", [{"file_name": "x.xlsx", "content": "..."}], producer="knowledge")
            else:
                ctx.set_output("evidence", [Evidence(statement="完整结果", source="x.xlsx", evidence_type="table")], producer="knowledge")
                ctx.set_output("sources", [{"file_name": "x.xlsx", "content": "..."}], producer="knowledge")
            return ctx

        orchestrator.knowledge_agent.execute = _make_execute_mock(knowledge_side_effect)
        orchestrator.answer_generator.execute = AsyncMock(side_effect=lambda ctx, **kw: ctx.set_output("answer", "最终答案", producer="generator") or ctx)

        critic_calls = 0

        async def critic_side_effect(ctx, **kw):
            nonlocal critic_calls
            critic_calls += 1
            if critic_calls == 1:
                ctx.set_output("critique", "证据不完整", producer="critic")
                ctx.set_output("need_retry", True, producer="critic")
                ctx.set_output("retry_target", "knowledge", producer="critic")
            else:
                ctx.set_output("critique", "通过", producer="critic")
                ctx.set_output("need_retry", False, producer="critic")
                ctx.set_output("retry_target", "all", producer="critic")
            return ctx

        orchestrator.critic_agent.execute = _make_execute_mock(critic_side_effect)

        result = await orchestrator.run(context)

        assert critic_calls == 2
        assert knowledge_call_count == 2
        assert result.get_output("answer") == "最终答案"
        mock_mcp_client.create_session.assert_called_once()
        mock_mcp_client.cleanup_session.assert_called_once()

    @pytest.mark.anyio
    async def test_critic_retry_preserves_evidence(self, orchestrator, mock_mcp_client):
        """Critic 重试 knowledge 后旧证据保留（去重合并）"""
        context = AgentContext(question="测试问题", session_id="s1", document_ids=[1])

        orchestrator.coordinator.needs_plan = False
        orchestrator.coordinator.needs_analysis = False
        orchestrator.coordinator.needs_review = True
        orchestrator.coordinator.execute = AsyncMock(return_value=context)

        knowledge_call_count = 0

        async def knowledge_side_effect(ctx, **kw):
            nonlocal knowledge_call_count
            knowledge_call_count += 1
            if knowledge_call_count == 1:
                # 第一次输出：evidence = [A, B]
                ctx.set_output("evidence", [
                    Evidence(statement="正确事实A", source="doc1.xlsx", evidence_type="table"),
                    Evidence(statement="正确事实B", source="doc2.xlsx", evidence_type="table"),
                ], producer="knowledge")
                ctx.set_output("sources", [
                    {"file_name": "doc1.xlsx", "content": "正确事实A"},
                    {"file_name": "doc2.xlsx", "content": "正确事实B"},
                ], producer="knowledge")
            else:
                # 第二次输出：evidence = [A, C]（A 重复，C 是新）
                ctx.set_output("evidence", [
                    Evidence(statement="正确事实A", source="doc1.xlsx", evidence_type="table"),
                    Evidence(statement="补充事实C", source="doc3.xlsx", evidence_type="table"),
                ], producer="knowledge")
                ctx.set_output("sources", [
                    {"file_name": "doc1.xlsx", "content": "正确事实A"},
                    {"file_name": "doc3.xlsx", "content": "补充事实C"},
                ], producer="knowledge")
            return ctx

        orchestrator.knowledge_agent.execute = _make_execute_mock(knowledge_side_effect)
        orchestrator.answer_generator.execute = AsyncMock(side_effect=lambda ctx, **kw: ctx.set_output("answer", "最终答案", producer="generator") or ctx)

        critic_calls = 0

        async def critic_side_effect(ctx, **kw):
            nonlocal critic_calls
            critic_calls += 1
            if critic_calls == 1:
                ctx.set_output("critique", "不完整", producer="critic")
                ctx.set_output("need_retry", True, producer="critic")
                ctx.set_output("retry_target", "knowledge", producer="critic")
            else:
                ctx.set_output("critique", "通过", producer="critic")
                ctx.set_output("need_retry", False, producer="critic")
                ctx.set_output("retry_target", "all", producer="critic")
            return ctx

        orchestrator.critic_agent.execute = _make_execute_mock(critic_side_effect)

        result = await orchestrator.run(context)

        # 验证证据合并：A(旧) + B(旧) + A(新重复) + C(新) = 3 条（去重后）
        evidence = result.get_output("evidence") or []
        assert len(evidence) == 3
        statements = {e.statement for e in evidence}
        assert "正确事实A" in statements
        assert "正确事实B" in statements
        assert "补充事实C" in statements

    @pytest.mark.anyio
    async def test_critic_retry_exhausted(self, orchestrator, mock_mcp_client):
        """Critic 重试 2 次仍拒绝，附加人工核实提示"""
        context = AgentContext(question="困难问题", session_id="s1", document_ids=[1])

        orchestrator.coordinator.needs_plan = False
        orchestrator.coordinator.needs_analysis = False
        orchestrator.coordinator.needs_review = True
        orchestrator.coordinator.execute = AsyncMock(return_value=context)

        async def knowledge_side_effect(ctx, **kw):
            ctx.set_output("evidence", [Evidence(statement="证据", source="x.xlsx", evidence_type="table")], producer="knowledge")
            ctx.set_output("sources", [{"file_name": "x.xlsx", "content": "..."}], producer="knowledge")
            return ctx
        orchestrator.knowledge_agent.execute = _make_execute_mock(knowledge_side_effect)
        orchestrator.answer_generator.execute = AsyncMock(side_effect=lambda ctx, **kw: ctx.set_output("answer", "不完美的答案", producer="generator") or ctx)

        async def critic_side_effect(ctx, **kw):
            ctx.set_output("critique", "仍然不好", producer="critic")
            ctx.set_output("need_retry", True, producer="critic")
            ctx.set_output("retry_target", "knowledge", producer="critic")
            return ctx
        orchestrator.critic_agent.execute = _make_execute_mock(critic_side_effect)

        result = await orchestrator.run(context)

        assert "人工核实" in (result.get_output("answer") or "")
        mock_mcp_client.cleanup_session.assert_called_once()

    @pytest.mark.anyio
    async def test_critic_retry_generator_target(self, orchestrator, mock_mcp_client):
        """Critic 要求重跑 generator，验证只重跑 generator"""
        context = AgentContext(question="测试", session_id="s1", document_ids=[1])

        orchestrator.coordinator.needs_plan = False
        orchestrator.coordinator.needs_analysis = False
        orchestrator.coordinator.needs_review = True
        orchestrator.coordinator.execute = AsyncMock(return_value=context)

        async def knowledge_side_effect(ctx, **kw):
            ctx.set_output("evidence", [Evidence(statement="证据", source="x.xlsx", evidence_type="table")], producer="knowledge")
            ctx.set_output("sources", [{"file_name": "x.xlsx", "content": "..."}], producer="knowledge")
            return ctx
        orchestrator.knowledge_agent.execute = _make_execute_mock(knowledge_side_effect)

        gen_call_count = 0

        async def gen_side_effect(ctx, **kw):
            nonlocal gen_call_count
            gen_call_count += 1
            if gen_call_count == 1:
                ctx.set_output("answer", "第一版答案", producer="generator")
            else:
                ctx.set_output("answer", "改进后的答案", producer="generator")
            return ctx
        orchestrator.answer_generator.execute = AsyncMock(side_effect=gen_side_effect)

        critic_calls = 0

        async def critic_side_effect(ctx, **kw):
            nonlocal critic_calls
            critic_calls += 1
            if critic_calls == 1:
                ctx.set_output("critique", "表达不好", producer="critic")
                ctx.set_output("need_retry", True, producer="critic")
                ctx.set_output("retry_target", "generator", producer="critic")
            else:
                ctx.set_output("critique", "通过", producer="critic")
                ctx.set_output("need_retry", False, producer="critic")
                ctx.set_output("retry_target", "all", producer="critic")
            return ctx
        orchestrator.critic_agent.execute = _make_execute_mock(critic_side_effect)

        result = await orchestrator.run(context)

        assert critic_calls == 2
        assert gen_call_count == 2
        assert result.get_output("answer") == "改进后的答案"


# ==================== MCP Session 生命周期 ====================

class TestMCPSessionLifecycle:

    @pytest.mark.anyio
    async def test_session_cleanup_on_success(self, orchestrator, mock_mcp_client):
        """正常流程: session 在 finally 中被清理"""
        context = AgentContext(question="测试", session_id="s1")

        orchestrator.coordinator.needs_plan = False
        orchestrator.coordinator.needs_analysis = False
        orchestrator.coordinator.needs_review = False
        orchestrator.coordinator.execute = AsyncMock(return_value=context)

        async def knowledge_side_effect(ctx, **kw):
            ctx.set_output("evidence", [], producer="knowledge")
            ctx.set_output("sources", [], producer="knowledge")
            return ctx
        orchestrator.knowledge_agent.execute = _make_execute_mock(knowledge_side_effect)
        orchestrator.answer_generator.execute = AsyncMock(side_effect=lambda ctx, **kw: ctx.set_output("answer", "答案", producer="generator") or ctx)

        await orchestrator.run(context)

        mock_mcp_client.create_session.assert_called_once()
        mock_mcp_client.cleanup_session.assert_called_once_with("test-session-uuid")

    @pytest.mark.anyio
    async def test_session_cleanup_on_agent_error(self, orchestrator, mock_mcp_client):
        """Agent 异常: session 仍然被清理"""
        context = AgentContext(question="会出错的问题", session_id="s1")

        orchestrator.coordinator.needs_plan = False
        orchestrator.coordinator.needs_analysis = False
        orchestrator.coordinator.needs_review = False
        orchestrator.coordinator.execute = AsyncMock(return_value=context)

        # Knowledge execute 抛异常（模拟 run 内部失败）
        async def knowledge_fail(ctx, **kw):
            raise RuntimeError("搜索失败")
        orchestrator.knowledge_agent.execute = _make_execute_mock(knowledge_fail)

        with pytest.raises(RuntimeError, match="搜索失败"):
            await orchestrator.run(context)

        # 即使异常，session 也被清理
        mock_mcp_client.create_session.assert_called_once()
        mock_mcp_client.cleanup_session.assert_called_once_with("test-session-uuid")

    @pytest.mark.anyio
    async def test_session_id_passed_to_set_document_ids(self, orchestrator, mock_mcp_client):
        """验证 set_document_ids 使用正确的 session_id"""
        context = AgentContext(question="测试", session_id="s1", document_ids=[10, 20, 30])

        orchestrator.coordinator.needs_plan = False
        orchestrator.coordinator.needs_analysis = False
        orchestrator.coordinator.needs_review = False
        orchestrator.coordinator.execute = AsyncMock(return_value=context)

        async def knowledge_side_effect(ctx, **kw):
            ctx.set_output("evidence", [], producer="knowledge")
            ctx.set_output("sources", [], producer="knowledge")
            return ctx
        orchestrator.knowledge_agent.execute = _make_execute_mock(knowledge_side_effect)
        orchestrator.answer_generator.execute = AsyncMock(side_effect=lambda ctx, **kw: ctx.set_output("answer", "答案", producer="generator") or ctx)

        await orchestrator.run(context)

        call_args = mock_mcp_client.call_tool.call_args
        assert call_args[1]["session_id"] == "test-session-uuid"
        assert call_args[0] == ("set_document_ids", {"ids": [10, 20, 30]})

    @pytest.mark.anyio
    async def test_no_document_ids_skips_set_document_ids(self, orchestrator, mock_mcp_client):
        """无 document_ids 时跳过 set_document_ids"""
        context = AgentContext(question="测试", session_id="s1", document_ids=None)

        orchestrator.coordinator.needs_plan = False
        orchestrator.coordinator.needs_analysis = False
        orchestrator.coordinator.needs_review = False
        orchestrator.coordinator.execute = AsyncMock(return_value=context)

        async def knowledge_side_effect(ctx, **kw):
            ctx.set_output("evidence", [], producer="knowledge")
            ctx.set_output("sources", [], producer="knowledge")
            return ctx
        orchestrator.knowledge_agent.execute = _make_execute_mock(knowledge_side_effect)
        orchestrator.answer_generator.execute = AsyncMock(side_effect=lambda ctx, **kw: ctx.set_output("answer", "答案", producer="generator") or ctx)

        await orchestrator.run(context)

        for call in mock_mcp_client.call_tool.call_args_list:
            assert call[0][0] != "set_document_ids"

    @pytest.mark.anyio
    async def test_same_session_across_retry(self, orchestrator, mock_mcp_client):
        """Critic 重试期间使用同一个 MCP session"""
        context = AgentContext(question="测试", session_id="s1", document_ids=[1])

        orchestrator.coordinator.needs_plan = False
        orchestrator.coordinator.needs_analysis = False
        orchestrator.coordinator.needs_review = True
        orchestrator.coordinator.execute = AsyncMock(return_value=context)

        async def knowledge_side_effect(ctx, **kw):
            ctx.set_output("evidence", [Evidence(statement="证据", source="x.xlsx", evidence_type="table")], producer="knowledge")
            ctx.set_output("sources", [{"file_name": "x.xlsx", "content": "..."}], producer="knowledge")
            return ctx
        orchestrator.knowledge_agent.execute = _make_execute_mock(knowledge_side_effect)
        orchestrator.answer_generator.execute = AsyncMock(side_effect=lambda ctx, **kw: ctx.set_output("answer", "答案", producer="generator") or ctx)

        session_ids_used = []

        async def critic_side_effect(ctx, **kw):
            session_ids_used.append(ctx.mcp_session_id)
            # 第一次拒绝
            if len(session_ids_used) == 1:
                ctx.set_output("critique", "不好", producer="critic")
                ctx.set_output("need_retry", True, producer="critic")
                ctx.set_output("retry_target", "knowledge", producer="critic")
            else:
                ctx.set_output("critique", "通过", producer="critic")
                ctx.set_output("need_retry", False, producer="critic")
                ctx.set_output("retry_target", "all", producer="critic")
            return ctx
        orchestrator.critic_agent.execute = _make_execute_mock(critic_side_effect)

        await orchestrator.run(context)

        # 两次 Critic 调用使用同一个 session_id
        assert len(session_ids_used) == 2
        assert session_ids_used[0] == session_ids_used[1] == "test-session-uuid"


# ==================== BaseAgent kwargs 转发 ====================

class TestBaseAgentKwargsForwarding:

    @pytest.mark.anyio
    async def test_execute_forwards_mcp_kwargs(self):
        """execute(mcp_client=..., mcp_session_id=...) 转发到 run()"""
        from app.core.agents.knowledge_agent import KnowledgeAgent

        agent = KnowledgeAgent.__new__(KnowledgeAgent)
        agent.engine = MagicMock()

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
    async def test_execute_no_extra_kwargs(self):
        """execute() 不传额外参数时 run() 正常"""
        from app.core.agents.knowledge_agent import KnowledgeAgent

        agent = KnowledgeAgent.__new__(KnowledgeAgent)
        agent.engine = MagicMock()

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

        orchestrator.coordinator.needs_plan = False
        orchestrator.coordinator.needs_analysis = False
        orchestrator.coordinator.needs_review = False
        orchestrator.coordinator.execute = AsyncMock(return_value=context)

        async def knowledge_side_effect(ctx, **kw):
            ctx.set_output("evidence", [], producer="knowledge")
            ctx.set_output("sources", [], producer="knowledge")
            return ctx
        orchestrator.knowledge_agent.execute = _make_execute_mock(knowledge_side_effect)
        orchestrator.answer_generator.execute = AsyncMock(side_effect=lambda ctx, **kw: ctx.set_output("answer", "答案", producer="generator") or ctx)

        mock_redis_store.safe_get_history = AsyncMock(return_value=[{"question": "之前的问题", "answer": "之前的答案"}])

        await orchestrator.run(context)

        mock_agent_memory.has_session.assert_called_with("user-123")
        mock_agent_memory.format_context.assert_called_with("user-123")

    @pytest.mark.anyio
    async def test_memory_updated_on_end(self, orchestrator, mock_agent_memory):
        """请求结束时更新记忆"""
        context = AgentContext(question="测试", session_id="user-123")
        context.set_output("sources", [{"file_name": "test.xlsx"}], producer="knowledge")

        orchestrator.coordinator.needs_plan = False
        orchestrator.coordinator.needs_analysis = False
        orchestrator.coordinator.needs_review = False
        orchestrator.coordinator.execute = AsyncMock(return_value=context)

        async def knowledge_side_effect(ctx, **kw):
            ctx.set_output("evidence", [], producer="knowledge")
            return ctx
        orchestrator.knowledge_agent.execute = _make_execute_mock(knowledge_side_effect)
        orchestrator.answer_generator.execute = AsyncMock(side_effect=lambda ctx, **kw: ctx.set_output("answer", "答案", producer="generator") or ctx)

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

        orchestrator.coordinator.needs_plan = False
        orchestrator.coordinator.needs_analysis = False
        orchestrator.coordinator.needs_review = False
        orchestrator.coordinator.execute = AsyncMock(return_value=context)

        async def knowledge_side_effect(ctx, **kw):
            ctx.set_output("evidence", [], producer="knowledge")
            ctx.set_output("sources", [], producer="knowledge")
            return ctx
        orchestrator.knowledge_agent.execute = _make_execute_mock(knowledge_side_effect)
        orchestrator.answer_generator.execute = AsyncMock(side_effect=lambda ctx, **kw: ctx.set_output("answer", "答案", producer="generator") or ctx)

        await orchestrator.run(context)

        mock_agent_memory.update.assert_not_called()

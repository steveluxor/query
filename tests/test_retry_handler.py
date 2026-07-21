"""RetryHandler 集成测试 — agent 名 → task ID 映射 + subgraph invalidation"""

from unittest.mock import MagicMock, AsyncMock

import pytest

from app.core.actions.retry import RetryHandler
from app.core.agent_context import AgentContext
from app.models.control import ControlAction
from app.models.task_graph import TaskGraph, TaskNode, TaskStatus


@pytest.fixture
def handler():
    return RetryHandler()


@pytest.fixture
def context():
    ctx = AgentContext(question="测试")
    ctx.plan = TaskGraph(
        goal="测试",
        tasks=[
            TaskNode(id="task1", agent="retrieval", objective="搜索", status=TaskStatus.COMPLETED),
            TaskNode(id="task2", agent="extractor", objective="提取", depends_on=["task1"], status=TaskStatus.COMPLETED),
            TaskNode(id="task3", agent="generator", objective="生成回答", depends_on=["task2"], status=TaskStatus.COMPLETED),
        ],
    )
    return ctx


@pytest.mark.anyio
async def test_retry_by_agent_name(handler, context):
    """按 agent 名 'retrieval' 重试 → 找到 task1，及其下游 task2, task3"""
    action = ControlAction(action_type="retry", target_task_id="retrieval")
    await handler.execute(action, context, None)

    task_map = {t.id: t for t in context.plan.tasks}
    assert task_map["task1"].status == TaskStatus.RETRYING
    assert task_map["task2"].status == TaskStatus.RETRYING
    assert task_map["task3"].status == TaskStatus.RETRYING


@pytest.mark.anyio
async def test_retry_generator_only(handler, context):
    """按 agent 名 'generator' 重试 → 只标记 task3"""
    action = ControlAction(action_type="retry", target_task_id="generator")
    await handler.execute(action, context, None)

    task_map = {t.id: t for t in context.plan.tasks}
    assert task_map["task1"].status == TaskStatus.COMPLETED  # 不受影响
    assert task_map["task2"].status == TaskStatus.COMPLETED  # 不受影响
    assert task_map["task3"].status == TaskStatus.RETRYING


@pytest.mark.anyio
async def test_retry_all(handler, context):
    """'all' → 全部标记为重试"""
    action = ControlAction(action_type="retry", target_task_id="all")
    await handler.execute(action, context, None)

    for t in context.plan.tasks:
        assert t.status == TaskStatus.RETRYING


@pytest.mark.anyio
async def test_retry_unknown_agent(handler, context):
    """未知 agent 名 → 不改变任何 task 状态"""
    action = ControlAction(action_type="retry", target_task_id="nonexistent_agent")
    await handler.execute(action, context, None)

    for t in context.plan.tasks:
        assert t.status == TaskStatus.COMPLETED


@pytest.mark.anyio
async def test_retry_parallel_branches(handler, context):
    """并行分支：retry 只影响目标分支，不影响独立分支"""
    context.plan = TaskGraph(
        goal="对比实验",
        tasks=[
            TaskNode(id="task1", agent="retrieval", objective="搜A", status=TaskStatus.COMPLETED),
            TaskNode(id="task2", agent="analysis", objective="分析B", status=TaskStatus.COMPLETED),
            TaskNode(id="task3", agent="extractor", objective="提取A", depends_on=["task1"], status=TaskStatus.COMPLETED),
            TaskNode(id="task4", agent="generator", objective="生成", depends_on=["task3", "task2"], status=TaskStatus.COMPLETED),
        ],
    )

    # 重试 retrieval 分支 — 只影响 retrieval 任务，不影响 analysis 分支
    action = ControlAction(action_type="retry", target_task_id="retrieval")
    await handler.execute(action, context, None)

    task_map = {t.id: t for t in context.plan.tasks}
    # task1（retrieval）及其下游被标记
    assert task_map["task1"].status == TaskStatus.RETRYING
    assert task_map["task3"].status == TaskStatus.RETRYING
    assert task_map["task4"].status == TaskStatus.RETRYING  # task4 依赖 task3
    # task2（analysis，独立分支）不受影响
    assert task_map["task2"].status == TaskStatus.COMPLETED


@pytest.mark.anyio
async def test_retry_no_plan(handler):
    """无 plan 时静默忽略"""
    context = AgentContext(question="测试")
    action = ControlAction(action_type="retry", target_task_id="retrieval")
    # 不应抛异常
    await handler.execute(action, context, None)


@pytest.mark.anyio
async def test_retry_empty_target(handler, context):
    """空 target 时静默忽略"""
    action = ControlAction(action_type="retry", target_task_id="")
    await handler.execute(action, context, None)
    # 状态不变
    for t in context.plan.tasks:
        assert t.status == TaskStatus.COMPLETED

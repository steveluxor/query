"""CodeAgent 单元测试"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.core.agent_context import AgentContext
from app.core.agents.code_agent import CodeAgent
from app.models.data_types import CodeResult


@pytest.fixture
def mock_llm():
    return MagicMock()


@pytest.fixture
def agent(mock_llm):
    return CodeAgent(mock_llm)


@pytest.fixture
def context():
    return AgentContext(question="按品牌统计平均价格")


SAMPLE_ROWS_TEXT = """以下是完整数据：

[Sales.xlsx / Sheet1]
1: 品牌: 万代, 产品: 高达, 价格: 299
2: 品牌: 万代, 产品: 魔神, 价格: 199
3: 品牌: TT, 产品: 蜘蛛侠, 价格: 59

以上为该文档全部数据。"""


def test_parse_rows_from_text(agent):
    """解析 read_all_rows 返回的文本"""
    rows = agent._parse_rows_from_text(SAMPLE_ROWS_TEXT)
    assert len(rows) == 3
    assert rows[0]["品牌"] == "万代"
    assert rows[0]["价格"] == 299
    assert rows[2]["品牌"] == "TT"
    assert rows[2]["价格"] == 59


def test_parse_rows_empty(agent):
    """空文本"""
    rows = agent._parse_rows_from_text("")
    assert rows == []


def test_parse_rows_no_data(agent):
    """无数据提示"""
    rows = agent._parse_rows_from_text("没有可读取的数据，请先调用 search_documents 搜索相关内容。")
    assert rows == []


NEW_FORMAT_TEXT = """以下是完整数据：

[账.xlsx / Sheet1]
[文件: 账.xlsx]
行号: 2
品牌: 高高
产品名: HG座天使2型
原价: 29.8
优惠: 0
结果: 29.8
: (空)

[账.xlsx / Sheet1]
[文件: 账.xlsx]
行号: 3
品牌: 万代
产品名: RG全装备创制强袭
原价: 145
优惠: 0
结果: 145

以上为该文档全部数据。"""


def test_parse_rows_from_text_new_format(agent):
    """解析新格式（多行 key: value）"""
    rows = agent._parse_rows_from_text(NEW_FORMAT_TEXT)
    assert len(rows) == 2
    assert rows[0]["品牌"] == "高高"
    assert rows[0]["结果"] == 29.8
    assert rows[1]["品牌"] == "万代"
    assert rows[1]["结果"] == 145
    assert ": (空)" not in str(rows)


def test_build_data_summary_with_data(agent):
    """构建数据摘要 — 有数据"""
    data = [
        {"品牌": "万代", "价格": 299},
        {"品牌": "TT", "价格": 59},
    ]
    summary = agent._build_data_summary(data)
    assert "数据行数: 2" in summary
    assert "品牌" in summary
    assert "价格" in summary


def test_build_data_summary_empty(agent):
    """构建数据摘要 — 空数据"""
    summary = agent._build_data_summary([])
    assert "无可用数据" in summary


@pytest.mark.anyio
async def test_generate_code(agent):
    """LLM 生成代码"""
    agent.llm = MagicMock()
    agent.llm.ainvoke = AsyncMock(return_value=MagicMock(
        content="result = sum(row['price'] for row in data)"
    ))
    code = await agent._generate_code("计算总价", "数据行数: 3\n列名: [price]")
    assert "result" in code
    assert "sum" in code


@pytest.mark.anyio
async def test_generate_code_strips_markdown(agent):
    """去除 markdown 代码块"""
    agent.llm = MagicMock()
    agent.llm.ainvoke = AsyncMock(return_value=MagicMock(
        content="```python\nresult = 1\n```"
    ))
    code = await agent._generate_code("test", "no data")
    assert code == "result = 1"


@pytest.mark.anyio
async def test_run_full_flow(agent, context):
    """完整 run() 流程"""
    mock_mcp = MagicMock()
    mock_mcp.call_tool = AsyncMock(return_value=SAMPLE_ROWS_TEXT)

    agent.llm = MagicMock()
    agent.llm.ainvoke = AsyncMock(return_value=MagicMock(
        content='result = {"total": sum(row["价格"] for row in data)}'
    ))

    with patch("app.core.agents.code_agent.CodeExecutor") as mock_executor:
        mock_executor.execute = AsyncMock(return_value={
            "result": {"total": 557},
            "stdout": "",
            "error": "",
            "success": True,
            "execution_time_ms": 10,
        })
        await agent.run(context, mcp_client=mock_mcp, mcp_session_id="s1")

    result = context.get_output("code_result")
    assert isinstance(result, CodeResult)
    assert result.success is True
    assert result.output == {"total": 557}


@pytest.mark.anyio
async def test_run_with_retry(agent, context):
    """执行失败后重试成功"""
    mock_mcp = MagicMock()
    mock_mcp.call_tool = AsyncMock(return_value=SAMPLE_ROWS_TEXT)

    agent.llm = MagicMock()
    # 第一次生成错误代码，第二次生成正确代码
    agent.llm.ainvoke = AsyncMock(side_effect=[
        MagicMock(content="result = 1 / 0"),
        MagicMock(content="result = 42"),
    ])

    call_count = 0

    async def fake_execute(code, context_vars, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return {"result": None, "stdout": "", "error": "ZeroDivisionError", "success": False, "execution_time_ms": 1}
        return {"result": 42, "stdout": "", "error": "", "success": True, "execution_time_ms": 1}

    with patch("app.core.agents.code_agent.CodeExecutor") as mock_executor:
        mock_executor.execute = AsyncMock(side_effect=fake_execute)
        await agent.run(context, mcp_client=mock_mcp, mcp_session_id="s1")

    result = context.get_output("code_result")
    assert result.success is True
    assert result.output == 42
    assert result.retry_count == 1


@pytest.mark.anyio
async def test_run_all_retries_fail(agent, context):
    """所有重试都失败"""
    mock_mcp = MagicMock()
    mock_mcp.call_tool = AsyncMock(return_value=SAMPLE_ROWS_TEXT)

    agent.llm = MagicMock()
    agent.llm.ainvoke = AsyncMock(return_value=MagicMock(
        content="result = 1 / 0"
    ))

    with patch("app.core.agents.code_agent.CodeExecutor") as mock_executor:
        mock_executor.execute = AsyncMock(return_value={
            "result": None, "stdout": "", "error": "ZeroDivisionError",
            "success": False, "execution_time_ms": 1,
        })
        await agent.run(context, mcp_client=mock_mcp, mcp_session_id="s1")

    result = context.get_output("code_result")
    assert result.success is False
    assert result.retry_count == 2


@pytest.mark.anyio
async def test_run_mcp_failure(agent, context):
    """MCP read_all_rows 失败时仍能执行"""
    mock_mcp = MagicMock()
    mock_mcp.call_tool = AsyncMock(side_effect=Exception("MCP 连接失败"))

    agent.llm = MagicMock()
    agent.llm.ainvoke = AsyncMock(return_value=MagicMock(
        content='result = {"msg": "无数据"}'
    ))

    with patch("app.core.agents.code_agent.CodeExecutor") as mock_executor:
        mock_executor.execute = AsyncMock(return_value={
            "result": {"msg": "无数据"}, "stdout": "", "error": "",
            "success": True, "execution_time_ms": 1,
        })
        await agent.run(context, mcp_client=mock_mcp, mcp_session_id="s1")

    result = context.get_output("code_result")
    assert result.success is True


def test_capability_definition(agent):
    """capability 定义正确"""
    cap = CodeAgent.capability
    assert cap.name == "code"
    assert "document_bundle" in cap.inputs
    assert "code_result" in cap.outputs
    assert cap.merge_policy["code_result"] == "replace"

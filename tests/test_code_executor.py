"""CodeExecutor 沙箱执行器单元测试"""

import pytest

from app.core.code_executor import CodeExecutor


@pytest.mark.anyio
async def test_execute_simple_code():
    """简单代码执行"""
    result = await CodeExecutor.execute(
        "result = 1 + 2",
        timeout=10,
    )
    assert result["success"] is True
    assert result["result"] == 3
    assert result["error"] == ""


@pytest.mark.anyio
async def test_execute_with_context_vars():
    """context_vars 传入"""
    result = await CodeExecutor.execute(
        "result = data[0]['price'] * quantity",
        context_vars={"data": [{"price": 10}], "quantity": 5},
        timeout=10,
    )
    assert result["success"] is True
    assert result["result"] == 50


@pytest.mark.anyio
async def test_execute_stdout_capture():
    """stdout 捕获"""
    result = await CodeExecutor.execute(
        "print('hello world')\nresult = 42",
        timeout=10,
    )
    assert result["success"] is True
    assert result["result"] == 42
    assert "hello world" in result["stdout"]


@pytest.mark.anyio
async def test_execute_syntax_error():
    """语法错误处理"""
    result = await CodeExecutor.execute(
        "def foo(\n  result = 1",
        timeout=10,
    )
    assert result["success"] is False
    assert "SyntaxError" in result["error"]


@pytest.mark.anyio
async def test_execute_runtime_error():
    """运行时错误处理"""
    result = await CodeExecutor.execute(
        "result = 1 / 0",
        timeout=10,
    )
    assert result["success"] is False
    assert "ZeroDivisionError" in result["error"]


@pytest.mark.anyio
async def test_execute_no_result_variable():
    """未设置 result 变量"""
    result = await CodeExecutor.execute(
        "x = 42",
        timeout=10,
    )
    assert result["success"] is True
    assert result["result"] is None


@pytest.mark.anyio
async def test_execute_dict_result():
    """dict 类型 result"""
    result = await CodeExecutor.execute(
        'result = {"total": 100, "count": 5}',
        timeout=10,
    )
    assert result["success"] is True
    assert result["result"]["total"] == 100
    assert result["result"]["count"] == 5


@pytest.mark.anyio
async def test_execute_import_allowed():
    """允许的模块导入"""
    result = await CodeExecutor.execute(
        "import json, math, collections\nresult = math.pi",
        timeout=10,
    )
    assert result["success"] is True
    assert abs(result["result"] - 3.14159) < 0.01


@pytest.mark.anyio
async def test_execute_import_blocked():
    """禁止的模块导入"""
    result = await CodeExecutor.execute(
        "import subprocess\nresult = 1",
        timeout=10,
    )
    assert result["success"] is False
    assert "不允许" in result["error"] or "ImportError" in result["error"]


@pytest.mark.anyio
async def test_execute_os_import_blocked():
    """禁止 os 模块"""
    result = await CodeExecutor.execute(
        "import os\nresult = 1",
        timeout=10,
    )
    assert result["success"] is False


@pytest.mark.anyio
async def test_execute_timeout():
    """超时处理"""
    result = await CodeExecutor.execute(
        "import time; time.sleep(60)",
        timeout=2,
    )
    assert result["success"] is False
    assert "超时" in result["error"]


@pytest.mark.anyio
async def test_execute_list_result():
    """list 类型 result"""
    result = await CodeExecutor.execute(
        "result = [i**2 for i in range(5)]",
        timeout=10,
    )
    assert result["success"] is True
    assert result["result"] == [0, 1, 4, 9, 16]


@pytest.mark.anyio
async def test_execute_data_processing():
    """模拟数据处理场景"""
    data = [
        {"brand": "A", "price": 100},
        {"brand": "B", "price": 200},
        {"brand": "A", "price": 150},
    ]
    code = """
from collections import defaultdict
groups = defaultdict(list)
for row in data:
    groups[row["brand"]].append(row["price"])
result = {k: sum(v)/len(v) for k, v in groups.items()}
"""
    result = await CodeExecutor.execute(code, context_vars={"data": data}, timeout=10)
    assert result["success"] is True
    assert result["result"]["A"] == 125.0
    assert result["result"]["B"] == 200.0

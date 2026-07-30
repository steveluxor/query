"""沙箱化 Python 代码执行器

在独立子进程中执行 Python 代码，限制超时、内存和可用模块。
"""

import asyncio
import json
import logging
import platform
import sys
import time

logger = logging.getLogger(__name__)

# 允许在沙箱中使用的标准库模块
_SAFE_MODULES = frozenset({
    "json", "math", "datetime", "collections", "itertools",
    "functools", "operator", "statistics", "re", "string",
    "decimal", "fractions", "copy", "pprint", "random",
    "enum", "dataclasses", "typing", "textwrap", "hashlib",
    "base64", "uuid", "calendar", "time",
    # 图表生成
    "matplotlib", "matplotlib.pyplot", "numpy",
})

# 子进程包装脚本 — 通过 stdin 接收 context_vars JSON，执行用户代码，输出结果 JSON
# 使用 @@PLACEHOLDER@@ 避免与 .format() / Python dict 语法冲突
_WRAPPER_TEMPLATE = r'''
import sys, json, io, importlib, platform

# --- 内存限制（仅 Linux） ---
@@MEMORY_LIMIT_CODE@@

# --- 读取输入 ---
_context_json = sys.stdin.read()
_input = json.loads(_context_json)
_user_code = _input["_user_code"]
context_vars = _input.get("context_vars", {})

# --- 限制 builtins ---
_safe = frozenset(@@SAFE_MODULES@@)

def _safe_import(name, *args, **kwargs):
    if isinstance(name, str):
        root = name.split(".")[0]
        if root in _safe:
            importlib.import_module(root)
            # 对 a.b.c 这样的路径，必须先导入完整路径，再返回根模块
            # 否则 import a.b.c as x 会失败（__import__ 需返回根模块供 Python 遍历属性链）
            if "." in name:
                importlib.import_module(name)
            return importlib.import_module(root)
    raise ImportError(f"模块 '{name}' 不允许在沙箱中使用")

_import_builtins = {k: v for k, v in __builtins__.__dict__.items()
                    if k not in ("__import__", "compile", "exec", "eval")}
_import_builtins["__import__"] = _safe_import

_ns = {"__builtins__": _import_builtins}
_ns.update(context_vars)

# --- 捕获 stdout ---
_old_stdout = sys.stdout
sys.stdout = _buf = io.StringIO()

try:
    exec(_user_code, _ns)
    _result = _ns.get("result")
    _out = json.dumps({
        "result": _result,
        "stdout": _buf.getvalue(),
        "error": "",
        "success": True,
    }, ensure_ascii=False, default=str)
except Exception as _e:
    _out = json.dumps({
        "result": None,
        "stdout": _buf.getvalue(),
        "error": f"{type(_e).__name__}: {_e}",
        "success": False,
    }, ensure_ascii=False, default=str)

sys.stdout = _old_stdout
print(_out)
'''


class CodeExecutor:
    """沙箱化 Python 代码执行器"""

    TIMEOUT = 60           # 秒
    MAX_MEMORY_MB = 512    # MB（matplotlib/numpy 需要更多内存）

    @staticmethod
    async def execute(
        code: str,
        context_vars: dict | None = None,
        timeout: int = TIMEOUT,
        max_memory_mb: int = MAX_MEMORY_MB,
    ) -> dict:
        """在子进程中执行 Python 代码

        Args:
            code: 要执行的 Python 代码（必须设置 result 变量）
            context_vars: 传入代码的变量 dict
            timeout: 超时秒数
            max_memory_mb: 内存上限 MB

        Returns:
            {"result": Any, "stdout": str, "error": str, "success": bool, "execution_time_ms": int}
        """
        start = time.time()

        # 构建内存限制代码（仅 Linux）
        if platform.system() == "Linux":
            max_bytes = max_memory_mb * 1024 * 1024
            memory_limit_code = (
                f"import resource\n"
                f"resource.setrlimit(resource.RLIMIT_AS, ({max_bytes}, {max_bytes}))"
            )
        else:
            memory_limit_code = "# 内存限制仅在 Linux 下生效"

        # 构建包装脚本（用 replace 避免 .format() 与 Python dict 语法冲突）
        wrapper = _WRAPPER_TEMPLATE.replace(
            "@@MEMORY_LIMIT_CODE@@", memory_limit_code,
        ).replace(
            "@@SAFE_MODULES@@", repr(set(_SAFE_MODULES)),
        )

        # 通过 stdin 传入 context_vars 和用户代码
        input_data = json.dumps({
            "context_vars": context_vars or {},
            "_user_code": code,
        }, ensure_ascii=False)

        try:
            import os
            exec_env = os.environ.copy()
            exec_env["OPENBLAS_NUM_THREADS"] = "4"
            exec_env["MKL_NUM_THREADS"] = "4"
            exec_env["OMP_NUM_THREADS"] = "4"

            proc = await asyncio.create_subprocess_exec(
                sys.executable, "-c", wrapper,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=exec_env,
            )

            stdout, stderr = await asyncio.wait_for(
                proc.communicate(input_data.encode("utf-8")),
                timeout=timeout,
            )

            elapsed = int((time.time() - start) * 1000)

            if proc.returncode != 0:
                error_msg = stderr.decode("utf-8", errors="replace").strip()
                logger.error("[CodeExecutor] 子进程退出码 %d: %s", proc.returncode, error_msg[:500])
                return {
                    "result": None,
                    "stdout": "",
                    "error": f"进程异常退出 (code={proc.returncode}): {error_msg[:500]}",
                    "success": False,
                    "execution_time_ms": elapsed,
                }

            # 解析 JSON 输出
            output_text = stdout.decode("utf-8", errors="replace").strip()
            result = json.loads(output_text)
            result["execution_time_ms"] = elapsed
            return result

        except asyncio.TimeoutError:
            elapsed = int((time.time() - start) * 1000)
            logger.warning("[CodeExecutor] 执行超时 (%ds)，终止进程", timeout)
            if proc:
                proc.kill()
                await proc.wait()
            return {
                "result": None,
                "stdout": "",
                "error": f"执行超时 ({timeout}秒)",
                "success": False,
                "execution_time_ms": elapsed,
            }
        except json.JSONDecodeError as e:
            elapsed = int((time.time() - start) * 1000)
            logger.error("[CodeExecutor] 输出解析失败: %s", e)
            return {
                "result": None,
                "stdout": "",
                "error": f"输出解析失败: {e}",
                "success": False,
                "execution_time_ms": elapsed,
            }
        except Exception as e:
            elapsed = int((time.time() - start) * 1000)
            logger.error("[CodeExecutor] 执行异常: %s", e)
            return {
                "result": None,
                "stdout": "",
                "error": f"执行异常: {type(e).__name__}: {e}",
                "success": False,
                "execution_time_ms": elapsed,
            }

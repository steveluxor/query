"""CodeAgent — 生成并执行 Python 代码进行复杂数据计算"""

import json
import logging
import os
import re
import time

from app.core.agents.base_agent import BaseAgent
from app.core.agent_context import AgentContext, _task_objective_var
from app.core.code_executor import CodeExecutor
from app.core.infra.llm_factory import create_llm
from app.core.mcp.client import MCPClient
from app.core.prompts.prompt_manager import PromptManager
from app.models.data_types import CodeResult, DocumentBundle
from app.models.capability import AgentCapability

logger = logging.getLogger(__name__)

MAX_RETRIES = 2


class CodeAgent(BaseAgent):
    """代码执行 Agent：通过 LLM 生成 Python 代码并在沙箱中执行"""

    name = "Code"
    capability = AgentCapability(
        name="code",
        description="复杂数据计算，通过生成和执行 Python 代码实现分组、过滤、统计等",
        inputs={
            "document_bundle": DocumentBundle,
        },
        outputs={
            "code_result": CodeResult,
        },
        tools=[],
        merge_policy={
            "code_result": "replace",
        },
    )

    def __init__(self, llm=None):
        self.llm = llm

    async def run(self, context: AgentContext, mcp_client: MCPClient = None,
                  mcp_session_id: str = "", document_bundle=None,
                  **kwargs) -> AgentContext:
        # 1. 调 MCP 获取完整数据
        data_rows = []
        try:
            raw_text = await mcp_client.call_tool(
                "read_all_rows", {}, session_id=mcp_session_id,
            )
            logger.info("[Code] read_all_rows 返回 %d 字符, 前500字: %s",
                        len(raw_text or ""), (raw_text or "")[:500])
            data_rows = self._parse_rows_from_text(raw_text)
            logger.info("[Code] 解析出 %d 行数据", len(data_rows))
        except Exception as e:
            logger.warning("[Code] read_all_rows 失败: %s", e)

        # 续跑时 MCP 子进程可能尚未恢复工具态；DAG 已注入的 Retrieval 输出是同一份 checkpoint 数据。
        if not data_rows and document_bundle and getattr(document_bundle, "chunks", None):
            bundle_text = "\n\n".join(
                f"[{chunk.source}]\n{chunk.content}" for chunk in document_bundle.chunks
            )
            data_rows = self._parse_rows_from_text(bundle_text)
            logger.warning("[Code] MCP 未返回数据，回退使用 DocumentBundle，解析出 %d 行", len(data_rows))

        context_vars = {
            "data": data_rows,
            "question": _task_objective_var.get() or context.question,
        }

        # 2. 构建数据摘要
        data_summary = self._build_data_summary(data_rows)

        # 4. Agent Loop: 生成代码 + 执行，失败重试
        code = ""
        result = None
        last_error = ""

        for attempt in range(MAX_RETRIES + 1):
            code = await self._generate_code(
                _task_objective_var.get() or context.question, data_summary, last_error,
            )
            t0 = time.time()
            exec_result = await CodeExecutor.execute(code, context_vars)
            elapsed = int((time.time() - t0) * 1000)

            if exec_result["success"]:
                # 提取图片路径并 base64 编码
                image_paths = []
                image_data = []
                exec_result_data = exec_result.get("result")
                if isinstance(exec_result_data, dict):
                    img = exec_result_data.get("image_path")
                    if img:
                        image_paths.append(img)
                        # 读取 PNG 并 base64 编码，然后删除本地文件
                        try:
                            import base64 as b64
                            with open(img, "rb") as f:
                                image_data.append(b64.b64encode(f.read()).decode())
                            os.remove(img)
                            logger.info("[Code] 图片已 base64 编码并清理: %s", img)
                        except Exception as e:
                            logger.warning("[Code] 读取/清理图片失败: %s", e)

                result = CodeResult(
                    code=code,
                    output=exec_result.get("result"),
                    stdout=exec_result.get("stdout", ""),
                    success=True,
                    execution_time_ms=elapsed,
                    retry_count=attempt,
                    image_paths=image_paths,
                    image_data=image_data,
                )
                break

            last_error = exec_result.get("error", "未知错误")
            logger.warning("[Code] 第 %d 次执行失败: %s", attempt + 1, last_error)

        # 全部重试失败
        if result is None:
            result = CodeResult(
                code=code,
                error=last_error,
                success=False,
                execution_time_ms=elapsed,
                retry_count=MAX_RETRIES,
            )

        context.set_output("code_result", result, producer="code")
        logger.info("[Code] 执行%s, 重试 %d 次, 耗时 %dms",
                     "成功" if result.success else "失败",
                     result.retry_count, result.execution_time_ms)
        return context

    def _parse_rows_from_text(self, text: str) -> list[dict]:
        """解析 read_all_rows 返回的文本为 list[dict]

        支持两种格式：
        1. 旧格式（单行）: "1: 品牌: 万代, 产品: 高达, 价格: 299"
        2. 新格式（多行）:
            [文件: 账.xlsx]
            行号: 2
            品牌: 高高
            产品名: HG座天使2型
            结果: 29.8
        """
        if not text or "没有可读取的数据" in text or "未找到完整数据" in text:
            return []

        rows = []
        current_file = ""
        current_row = {}

        for line in text.split("\n"):
            line = line.strip()
            if not line:
                # 空行：如果 current_row 有数据，保存并重置
                if current_row:
                    rows.append(current_row)
                    current_row = {}
                continue

            # 跳过说明行
            if line.startswith("以下是") or line.startswith("以上为"):
                continue

            # 文件头: [Sales.xlsx / Sheet1] 或 [文件: 账.xlsx]
            file_match = re.match(r"^\[(.+?)(?:\s*/\s*(.+?))?\]$", line)
            if file_match:
                current_file = file_match.group(1)
                continue

            # 新格式: "行号: 2" — 标志新记录开始
            if re.match(r"^行号:\s*\d+", line):
                if current_row:
                    rows.append(current_row)
                    current_row = {}
                if current_file:
                    current_row["文件"] = current_file
                continue

            # 旧格式: "N: key1: value1, key2: value2" — 必须在 kv_match 之前
            row_match = re.match(r"^\d+:\s*(.+)$", line)
            if row_match:
                if current_row:
                    rows.append(current_row)
                    current_row = {}
                row = {"文件": current_file} if current_file else {}
                pairs = row_match.group(1)
                for part in pairs.split(", "):
                    if ": " in part:
                        k, v = part.split(": ", 1)
                        try:
                            v = int(v)
                        except ValueError:
                            try:
                                v = float(v)
                            except ValueError:
                                pass
                        row[k] = v
                if row:
                    rows.append(row)
                continue

            # 新格式: "key: value"
            kv_match = re.match(r"^(.+?):\s*(.+)$", line)
            if kv_match:
                k = kv_match.group(1).strip()
                v = kv_match.group(2).strip()
                # 跳过空值
                if v in ("(空)", ""):
                    continue
                # 尝试转数值
                try:
                    v = int(v)
                except ValueError:
                    try:
                        v = float(v)
                    except ValueError:
                        pass
                current_row[k] = v
                continue

        # 保存最后一行
        if current_row:
            rows.append(current_row)

        return rows

    def _build_data_summary(self, data_rows: list[dict]) -> str:
        """构建数据摘要供 LLM 生成代码"""
        parts = []

        if data_rows:
            all_keys = set()
            for row in data_rows[:10]:
                all_keys.update(row.keys())
            keys = sorted(all_keys)

            parts.append(f"数据行数: {len(data_rows)}")
            parts.append(f"列名: {keys}")
            parts.append("前3行示例:")
            for row in data_rows[:3]:
                parts.append(f"  {row}")
        else:
            parts.append("无可用数据（data 为空列表）")

        return "\n".join(parts)

    async def _generate_code(self, question: str, data_summary: str,
                             last_error: str = "") -> str:
        """调用 LLM 生成 Python 代码"""
        system_prompt = PromptManager.get("code", "system")

        user_prompt = f"数据摘要：\n{data_summary}\n\n用户问题：{question}"
        if last_error:
            user_prompt += f"\n\n上一次执行出错：{last_error}\n请修复代码后重新生成。"

        llm = self.llm or create_llm(temperature=0, max_tokens=4096, timeout=120)
        result = await llm.ainvoke([
            ("system", system_prompt),
            ("human", user_prompt),
        ])

        code = result.content.strip()
        # 去除 markdown 代码块包裹
        if code.startswith("```python"):
            code = code[len("```python"):]
        elif code.startswith("```"):
            code = code[3:]
        if code.endswith("```"):
            code = code[:-3]

        code = code.strip()
        logger.info("[Code] 生成代码:\n%s", code[:1000])
        return code

#!/usr/bin/env python
"""Reflection 测试 — 验证反思机制是否正常工作，结果保存到 tests/reports/"""

import sys
import os
import httpx
from datetime import datetime

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

API_BASE = "http://localhost:8000"
REPORTS_DIR = os.path.join(os.path.dirname(__file__), "reports")


def run_test(name: str, question: str, expect_reflection: bool | None, session_id: str = None) -> dict:
    """执行单条测试，返回结果。expect_reflection=None 表示只记录不判断"""
    if not session_id:
        session_id = f"test_reflect_{name}"
    resp = httpx.post(f"{API_BASE}/qa/ask", json={
        "question": question,
        "session_id": session_id,
    }, timeout=120)
    data = resp.json()
    reflection_count = data.get("reflection_count", 0)
    if expect_reflection is None:
        passed = True
    else:
        passed = (reflection_count > 0) == expect_reflection
    return {
        "name": name,
        "question": question,
        "answer": data.get("answer", ""),
        "tools_called": data.get("tools_called", []),
        "reflection_count": reflection_count,
        "expect_reflection": expect_reflection,
        "passed": passed,
    }


def main():
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    lines = []

    def log(text=""):
        print(text)
        lines.append(text)

    log("=" * 60)
    log(f"Reflection 评测报告 ({timestamp})")
    log("=" * 60)
    log()

    tests = [
        # (name, question, expect_reflection)
        ("simple_001", "你好", False),
        ("simple_002", "谢谢", False),
        ("simple_003", "你是谁", False),
        ("calc_001", "账.xlsx里总共花了多少钱", None),
        ("calc_002", "账.xlsx里最贵的是什么", None),
        ("search_001", "有哪些品牌的商品", None),
    ]

    results = []
    for name, question, expect in tests:
        r = run_test(name, question, expect)
        status = "PASS" if r["passed"] else "FAIL"
        log(f"[{status}] {r['name']}: {r['question']}")
        expect_str = "是" if r["expect_reflection"] else ("否" if r["expect_reflection"] is False else "任意")
        log(f"      期望reflection={expect_str} 实际reflection_count={r['reflection_count']} 工具={r['tools_called']}")
        log(f"      回答: {r['answer'][:150]}")
        log()
        results.append(r)

    # 统计
    log("=" * 60)
    passed = sum(1 for r in results if r["passed"])
    failed = len(results) - passed
    log(f"总题数: {len(results)}  通过: {passed}  失败: {failed}  通过率: {passed/len(results)*100:.1f}%")
    log()

    # 按类别统计
    log("按类别:")
    simple = [r for r in results if r["name"].startswith("simple")]
    calc = [r for r in results if r["name"].startswith("calc")]
    search = [r for r in results if r["name"].startswith("search")]
    log(f"  简单问题: {sum(1 for r in simple if r['passed'])}/{len(simple)}")
    log(f"  计算问题: {sum(1 for r in calc if r['passed'])}/{len(calc)}")
    log(f"  搜索问题: {sum(1 for r in search if r['passed'])}/{len(search)}")

    # 保存报告文件
    os.makedirs(REPORTS_DIR, exist_ok=True)
    report_path = os.path.join(REPORTS_DIR, f"reflection_{timestamp}.txt")
    with open(report_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    print(f"\n报告已保存: {report_path}")

    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())

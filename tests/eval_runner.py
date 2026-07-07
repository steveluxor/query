#!/usr/bin/env python
"""RAG 工具调用评测脚本 — 通过 API 评估 LLM 的工具选择与检索质量"""

import json
import sys
from datetime import datetime
from pathlib import Path

import httpx

# 确保终端能输出中文
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

API_BASE = "http://localhost:8000"
QUESTIONS_PATH = Path(__file__).parent / "test_data" / "eval_questions.json"


def load_questions() -> list[dict]:
    with open(QUESTIONS_PATH, encoding="utf-8") as f:
        return json.load(f)


def run_eval():
    print("=" * 60)
    print("RAG 工具调用评测 (via API)")
    print("=" * 60)
    print()

    questions = load_questions()

    results = []
    passed = 0
    failed = 0

    with httpx.Client(base_url=API_BASE, timeout=120) as client:
        for q in questions:
            qid = q["id"]
            question = q["question"]
            expected = q.get("expected_tool_type", "")

            print(f"[{qid}] {question}")
            print(f"  期望: {expected}")

            try:
                resp = client.post("/qa/ask", json={"question": question})
                data = resp.json()
                answer = data.get("answer", "")
                sources = data.get("sources", [])
                tools_called = data.get("tools_called", [])

                # 根据实际工具调用序列推断工具类型
                source_names = [s.get("file_name", "") for s in sources]
                if not tools_called:
                    actual_tool = "none"
                elif "calculate_sum" in tools_called:
                    actual_tool = "search_then_sum"
                elif "calculate_rank" in tools_called:
                    actual_tool = "search_then_rank"
                elif "read_all_rows" in tools_called:
                    actual_tool = "search_then_read_all"
                elif "search_documents" in tools_called:
                    actual_tool = "search"
                else:
                    actual_tool = "none"

                has_error = "错误" in answer or "抱歉" in answer

                # 答案内容校验（OR 逻辑：至少匹配一个关键词即通过）
                expected_kw = q.get("expected_answer_contains", [])
                kw_matched = []
                kw_missing = []
                for kw in expected_kw:
                    if kw in answer:
                        kw_matched.append(kw)
                    else:
                        kw_missing.append(kw)
                answer_ok = not expected_kw or len(kw_matched) > 0

                tool_ok = actual_tool == expected and not has_error
                is_passed = tool_ok and answer_ok
                status = "[PASS]" if is_passed else "[FAIL]"

                print(f"  实际: {actual_tool}  |  工具调用: {tools_called}  |  来源: {source_names}")
                if expected_kw:
                    if kw_matched:
                        print(f"  关键词: ✓{kw_matched}")
                    if kw_missing:
                        print(f"  关键词缺失: {kw_missing}")
                print(f"  回答: {answer[:100]}...")
                print(f"  状态: {status}")
                print()

                row = {
                    "id": qid,
                    "question": question,
                    "answer": answer,
                    "expected": expected,
                    "actual": actual_tool,
                    "tools_called": tools_called,
                    "sources": source_names,
                    "has_error": has_error,
                    "answer_ok": answer_ok,
                    "kw_matched": kw_matched,
                    "kw_missing": kw_missing,
                    "passed": is_passed,
                }
                results.append(row)
                if row["passed"]:
                    passed += 1
                else:
                    failed += 1

            except Exception as e:
                print(f"  异常: {e}\n")
                results.append({"id": qid, "question": question, "answer": "", "expected": expected, "actual": "error", "has_error": True, "passed": False, "kw_matched": [], "kw_missing": []})
                failed += 1

    # 汇总
    print("=" * 60)
    print(f"总题数: {len(questions)}  通过: {passed}  失败: {failed}  通过率: {passed / len(questions) * 100:.0f}%")

    groups = {}
    for r in results:
        g = next((q["group"] for q in questions if q["id"] == r["id"]), "其他")
        groups.setdefault(g, {"total": 0, "passed": 0})
        groups[g]["total"] += 1
        if r["passed"]:
            groups[g]["passed"] += 1
    print()
    print("按类别:")
    for g, s in groups.items():
        print(f"  {g}: {s['passed']}/{s['total']}")
    print("=" * 60)

    # 保存结果
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    report_dir = Path(__file__).parent / "reports"
    report_dir.mkdir(exist_ok=True)

    summary = {
        "timestamp": timestamp,
        "total": len(questions),
        "passed": passed,
        "failed": failed,
        "pass_rate": round(passed / len(questions) * 100, 1) if questions else 0,
        "results": results,
        "groups": {g: s for g, s in groups.items()},
    }

    json_path = report_dir / f"eval_{timestamp}.json"
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)
    print(f"结果已保存: {json_path}")

    # 生成简短文本报告
    txt_path = report_dir / f"eval_{timestamp}.txt"
    with open(txt_path, "w", encoding="utf-8") as f:
        f.write(f"RAG 评测报告 ({timestamp})\n")
        f.write("=" * 50 + "\n")
        f.write(f"总题数: {len(questions)}  通过: {passed}  失败: {failed}  通过率: {summary['pass_rate']}%\n\n")
        for r in results:
            flag = "PASS" if r["passed"] else "FAIL"
            f.write(f"[{flag}] {r['id']}: {r['question']}\n")
            f.write(f"      期望={r['expected']} 实际={r['actual']} 工具={r.get('tools_called', [])} 来源={r['sources']}\n")
            if r["kw_matched"] or r["kw_missing"]:
                kw_text = f"关键词命中={r['kw_matched']}"
                if r["kw_missing"]:
                    kw_text += f" 缺失={r['kw_missing']}"
                f.write(f"      {kw_text}\n")
            f.write(f"      回答: {r['answer'][:200]}\n")
            f.write("\n")
        f.write("=" * 50 + "\n")
        f.write("按类别:\n")
        for g, s in groups.items():
            f.write(f"  {g}: {s['passed']}/{s['total']}\n")
    print(f"报告已保存: {txt_path}")

    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(run_eval())

#!/usr/bin/env python
"""偏好替换测试 — 删除旧偏好后设置新偏好，验证新偏好生效、旧偏好不残留"""

import sys

import httpx

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

API_BASE = "http://localhost:8000"


def test_preference_reassign():
    print("=" * 60)
    print("偏好替换测试：老板 → 删除 → 老师")
    print("=" * 60)
    print()

    session_id = "test_pref_reassign_001"
    passed = 0
    failed = 0

    with httpx.Client(base_url=API_BASE, timeout=120) as client:

        # --- 阶段 1：设置旧偏好 ---
        print("[阶段 1] 设置旧偏好：叫我老板")
        resp = client.post("/qa/ask", json={
            "question": "从现在开始每次回答都要在开头加上老板",
            "session_id": session_id,
        })
        data = resp.json()
        prefs = data.get("memory_data", {}).get("preferences") or {}
        print(f"  回答: {data.get('answer', '')[:100]}")
        print(f"  偏好: {prefs}")
        if prefs.get("address_as") == "老板":
            print("  状态: [PASS]\n")
            passed += 1
        else:
            print("  状态: [FAIL]\n")
            failed += 1

        # --- 阶段 2：删除旧偏好 ---
        print("[阶段 2] 删除旧偏好：不要叫老板了")
        resp = client.post("/qa/ask", json={
            "question": "不要加上老板了",
            "session_id": session_id,
        })
        data = resp.json()
        prefs = data.get("memory_data", {}).get("preferences") or {}
        print(f"  回答: {data.get('answer', '')[:100]}")
        print(f"  偏好: {prefs}")
        if "address_as" not in prefs:
            print("  状态: [PASS] 旧偏好已删除\n")
            passed += 1
        else:
            print("  状态: [FAIL] 旧偏好未删除\n")
            failed += 1

        # --- 阶段 3：确认删除生效（回答中无老板） ---
        print("[阶段 3] 确认删除生效")
        resp = client.post("/qa/ask", json={
            "question": "你好",
            "session_id": session_id,
        })
        data = resp.json()
        answer3 = data.get("answer", "")
        print(f"  回答: {answer3[:100]}")
        if "老板" not in answer3:
            print("  状态: [PASS] 回答中无「老板」\n")
            passed += 1
        else:
            print("  状态: [FAIL] 回答中仍有「老板」\n")
            failed += 1

        # --- 阶段 4：设置新偏好 ---
        print("[阶段 4] 设置新偏好：叫我老师")
        resp = client.post("/qa/ask", json={
            "question": "从现在开始每次回答都要在开头加上老师",
            "session_id": session_id,
        })
        data = resp.json()
        prefs = data.get("memory_data", {}).get("preferences") or {}
        print(f"  回答: {data.get('answer', '')[:100]}")
        print(f"  偏好: {prefs}")
        if prefs.get("address_as") == "老师":
            print("  状态: [PASS] 新偏好已设置\n")
            passed += 1
        else:
            print("  状态: [FAIL] 新偏好未设置\n")
            failed += 1

        # --- 阶段 5：验证新偏好生效 ---
        print("[阶段 5] 验证新偏好生效")
        resp = client.post("/qa/ask", json={
            "question": "你好",
            "session_id": session_id,
        })
        data = resp.json()
        answer5 = data.get("answer", "")
        print(f"  回答: {answer5[:100]}")
        has_teacher = "老师" in answer5
        has_boss = "老板" in answer5
        if has_teacher and not has_boss:
            print("  状态: [PASS] 回答中有「老师」无「老板」\n")
            passed += 1
        else:
            print(f"  状态: [FAIL] 老师={has_teacher} 老板={has_boss}\n")
            failed += 1

        # --- 阶段 6~12：多轮对话，等待 rewrite 触发（REWRITE_INTERVAL=10） ---
        round_questions = [
            "谢谢", "帮我查一下数据", "今天天气怎么样", "你好",
            "最近有什么新闻", "帮我总结一下", "再见",
        ]
        found_teacher = False
        for i, q in enumerate(round_questions, start=6):
            resp = client.post("/qa/ask", json={"question": q, "session_id": session_id})
            data = resp.json()
            prefs = data.get("memory_data", {}).get("preferences") or {}
            answer = data.get("answer", "")
            has_teacher = "老师" in answer
            has_boss = "老板" in answer
            teacher_pref = prefs.get("address_as")
            tag = ""
            if teacher_pref == "老师":
                tag = " ← 偏好已被 rewrite 提取"
                found_teacher = True
            print(f"[阶段 {i}] 问: {q}")
            print(f"  回答: {answer[:80]}")
            print(f"  偏好: {prefs}{tag}")
            if i == 6:
                # 第 6 轮：偏好可能还没被提取，只检查不计分
                if teacher_pref == "老师":
                    print("  状态: [PASS] 新偏好已生效\n")
                    passed += 1
                else:
                    print("  状态: [INFO] 偏好尚未被提取，等待 rewrite\n")
            elif i == 12:
                # 最后一轮：最终验证
                if teacher_pref == "老师" and has_teacher and not has_boss:
                    print("  状态: [PASS] rewrite 已提取新偏好，回答正确\n")
                    passed += 1
                else:
                    print(f"  状态: [FAIL] prefs={teacher_pref} 老师={has_teacher} 老板={has_boss}\n")
                    failed += 1
            else:
                print()

        if not found_teacher:
            print("  [WARN] 整个测试中偏好从未被提取\n")

    print("=" * 60)
    print(f"总测试: {passed + failed}  通过: {passed}  失败: {failed}")
    print("=" * 60)
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(test_preference_reassign())

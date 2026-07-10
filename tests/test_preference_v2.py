#!/usr/bin/env python
"""偏好设置与删除测试 v2 — 用「叫我老板」做例子，删除后多轮确认"""

import json
import sys

import httpx

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

API_BASE = "http://localhost:8000"


def test_preference_lifecycle():
    print("=" * 60)
    print("偏好设置与删除测试 v2（叫我老板）")
    print("=" * 60)
    print()

    session_id = "test_pref_v2_001"
    passed = 0
    failed = 0

    with httpx.Client(base_url=API_BASE, timeout=120) as client:

        # --- 阶段 1：设置偏好 ---
        print("[阶段 1] 设置偏好：回答开头叫我老板")
        resp = client.post("/qa/ask", json={
            "question": "从现在开始每次回答都要在开头加上老板",
            "session_id": session_id,
        })
        data = resp.json()
        mem = data.get("memory_data") or {}
        prefs = mem.get("preferences") or {}
        answer1 = data.get("answer", "")
        print(f"  回答: {answer1[:150]}")
        print(f"  偏好: {prefs}")
        if prefs.get("address_as") == "老板":
            print("  状态: [PASS] 偏好已设置\n")
            passed += 1
        else:
            print("  状态: [FAIL] 偏好未设置\n")
            failed += 1

        # --- 阶段 2：验证偏好生效 ---
        print("[阶段 2] 验证偏好生效：问一个普通问题")
        resp = client.post("/qa/ask", json={
            "question": "你好",
            "session_id": session_id,
        })
        data = resp.json()
        answer2 = data.get("answer", "")
        print(f"  回答: {answer2[:150]}")
        has_boss = "老板" in answer2
        if has_boss:
            print("  状态: [PASS] 回答中包含「老板」\n")
            passed += 1
        else:
            print("  状态: [FAIL] 回答中未包含「老板」\n")
            failed += 1

        # --- 阶段 3：取消偏好（自然语言） ---
        print("[阶段 3] 取消偏好：不要加老板了")
        resp = client.post("/qa/ask", json={
            "question": "不要加上老板了",
            "session_id": session_id,
        })
        data = resp.json()
        mem = data.get("memory_data") or {}
        prefs = mem.get("preferences") or {}
        answer3 = data.get("answer", "")
        print(f"  回答: {answer3[:150]}")
        print(f"  偏好: {prefs}")
        pref_still_exists = "address_as" in prefs
        print(f"  偏好仍存在: {pref_still_exists}")

        # --- 阶段 4：确认偏好已删除（第1轮） ---
        print("[阶段 4] 确认偏好已删除（第1轮）")
        resp = client.post("/qa/ask", json={
            "question": "今天天气怎么样",
            "session_id": session_id,
        })
        data = resp.json()
        mem = data.get("memory_data") or {}
        prefs = mem.get("preferences") or {}
        answer4 = data.get("answer", "")
        print(f"  回答: {answer4[:150]}")
        print(f"  偏好: {prefs}")
        if "address_as" not in prefs:
            print("  状态: [PASS] 偏好已删除\n")
            passed += 1
        else:
            print("  状态: [FAIL] 偏好未删除\n")
            failed += 1

        # --- 阶段 5：验证回答不再包含老板（第1轮） ---
        print("[阶段 5] 验证回答不再包含老板（第1轮）")
        has_boss_4 = "老板" in answer4
        if not has_boss_4:
            print("  状态: [PASS] 回答中无「老板」\n")
            passed += 1
        else:
            print("  状态: [FAIL] 回答中仍有「老板」\n")
            failed += 1

        # --- 阶段 6：再问一轮（第2轮） ---
        print("[阶段 6] 再问一轮（第2轮）")
        resp = client.post("/qa/ask", json={
            "question": "帮我查一下最近的订单",
            "session_id": session_id,
        })
        data = resp.json()
        mem = data.get("memory_data") or {}
        prefs = mem.get("preferences") or {}
        answer6 = data.get("answer", "")
        print(f"  回答: {answer6[:150]}")
        print(f"  偏好: {prefs}")
        has_boss_6 = "老板" in answer6
        if not has_boss_6:
            print("  状态: [PASS] 回答中无「老板」\n")
            passed += 1
        else:
            print("  状态: [FAIL] 回答中仍有「老板」\n")
            failed += 1

        # --- 阶段 7：再问一轮（第3轮） ---
        print("[阶段 7] 再问一轮（第3轮）")
        resp = client.post("/qa/ask", json={
            "question": "谢谢",
            "session_id": session_id,
        })
        data = resp.json()
        mem = data.get("memory_data") or {}
        prefs = mem.get("preferences") or {}
        answer7 = data.get("answer", "")
        print(f"  回答: {answer7[:150]}")
        print(f"  偏好: {prefs}")
        if "address_as" not in prefs:
            print("  状态: [PASS] 偏好仍然不存在\n")
            passed += 1
        else:
            print("  状态: [FAIL] 偏好被重新设置了\n")
            failed += 1

        has_boss_7 = "老板" in answer7
        if not has_boss_7:
            print("  状态: [PASS] 回答中无「老板」\n")
            passed += 1
        else:
            print("  状态: [FAIL] 回答中仍有「老板」\n")
            failed += 1

        # --- 阶段 8：重复取消（确保幂等） ---
        print("[阶段 8] 重复取消：以后都不要叫我老板了")
        resp = client.post("/qa/ask", json={
            "question": "以后都不要叫我老板了",
            "session_id": session_id,
        })
        data = resp.json()
        mem = data.get("memory_data") or {}
        prefs = mem.get("preferences") or {}
        print(f"  回答: {data.get('answer', '')[:150]}")
        print(f"  偏好: {prefs}")
        if "address_as" not in prefs:
            print("  状态: [PASS] 偏好仍然不存在\n")
            passed += 1
        else:
            print("  状态: [FAIL] 偏好被重新设置了\n")
            failed += 1

    print("=" * 60)
    print(f"总测试: {passed + failed}  通过: {passed}  失败: {failed}")
    print("=" * 60)
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(test_preference_lifecycle())

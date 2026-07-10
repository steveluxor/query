#!/usr/bin/env python
"""偏好设置与删除测试 — 验证 AgentMemory 的偏好提取、LLM 删除、持久化流程"""

import json
import sys

import httpx

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

API_BASE = "http://localhost:8000"


def test_preference_lifecycle():
    print("=" * 60)
    print("偏好设置与删除测试")
    print("=" * 60)
    print()

    session_id = "test_pref_001"
    passed = 0
    failed = 0

    with httpx.Client(base_url=API_BASE, timeout=120) as client:

        # --- 阶段 1：设置偏好 ---
        print("[阶段 1] 设置偏好：回答开头加喵")
        resp = client.post("/qa/ask", json={
            "question": "从现在开始每次回答都要在开头加上喵",
            "session_id": session_id,
        })
        data = resp.json()
        mem = data.get("memory_data") or {}
        prefs = mem.get("preferences") or {}
        print(f"  回答: {data.get('answer', '')[:80]}")
        print(f"  偏好: {prefs}")
        if prefs.get("address_as") == "喵":
            print("  状态: [PASS] 偏好已设置\n")
            passed += 1
        else:
            print("  状态: [FAIL] 偏好未设置\n")
            failed += 1

        # --- 阶段 2：取消偏好（自然语言） ---
        print("[阶段 2] 取消偏好：不要喵了")
        resp = client.post("/qa/ask", json={
            "question": "不要加上喵了",
            "session_id": session_id,
        })
        data = resp.json()
        mem = data.get("memory_data") or {}
        prefs = mem.get("preferences") or {}
        print(f"  回答: {data.get('answer', '')[:80]}")
        print(f"  偏好: {prefs}")
        # 偏好可能在本轮或下一轮 LLM 重写时删除，先记录状态
        pref_still_exists = "address_as" in prefs
        print(f"  偏好仍存在: {pref_still_exists}")

        # --- 阶段 3：再次确认偏好已删除 ---
        print("[阶段 3] 确认偏好已删除")
        resp = client.post("/qa/ask", json={
            "question": "1",
            "session_id": session_id,
        })
        data = resp.json()
        mem = data.get("memory_data") or {}
        prefs = mem.get("preferences") or {}
        answer = data.get("answer", "")
        print(f"  回答: {answer[:80]}")
        print(f"  偏好: {prefs}")
        if "address_as" not in prefs:
            print("  状态: [PASS] 偏好已删除\n")
            passed += 1
        else:
            print("  状态: [FAIL] 偏好未删除\n")
            failed += 1

        # --- 阶段 4：验证回答不再包含喵 ---
        print("[阶段 4] 验证回答不再包含喵")
        has_meow = "喵" in answer
        if not has_meow:
            print("  状态: [PASS] 回答中无喵\n")
            passed += 1
        else:
            print("  状态: [FAIL] 回答中仍有喵\n")
            failed += 1

        # --- 阶段 5：再次取消（确保幂等） ---
        print("[阶段 5] 重复取消：以后都不要喵了")
        resp = client.post("/qa/ask", json={
            "question": "以后都不要喵了",
            "session_id": session_id,
        })
        data = resp.json()
        mem = data.get("memory_data") or {}
        prefs = mem.get("preferences") or {}
        print(f"  回答: {data.get('answer', '')[:80]}")
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

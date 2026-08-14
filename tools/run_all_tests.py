# -*- coding: utf-8 -*-
"""一键运行全部上位机回归测试（U3 固化，2026-08-14）。

用法:
    python tools/run_all_tests.py            # 全部运行
    python tools/run_all_tests.py --list     # 列出测试
    python tools/run_all_tests.py smoke      # 只跑指定子集

说明:
    - 所有测试 offscreen 模式运行，无需显示器
    - 逐个顺序执行，汇总通过/失败，任一步失败返回非零退出码（可接 CI）
    - 每个测试独立子进程运行，避免 import 状态污染
"""
import os
import subprocess
import sys
import time

HOST_PC = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PYTHON = sys.executable

# (脚本名, 简述, 预计秒)
TESTS = [
    ("tools/smoke_test.py",        "主窗口+5页+模拟流+历史+PLC", 90),
    ("tools/test_no_sim_start.py", "无图/无连接不启模拟流+本地推理", 120),
    ("tools/test_model_mgr.py",    "模型管理 v3 全功能", 60),
    # 通信调试（需 Nano 在线，失败仅告警不阻断）
    ("tools/debug_comm_test.py",   "TCP 心跳/模型列表/推理", 60),
]


def run_one(name: str, desc: str, timeout: int) -> bool:
    path = os.path.join(HOST_PC, name)
    env = dict(os.environ)
    env["QT_QPA_PLATFORM"] = "offscreen"
    env.setdefault("PYTHONUTF8", "1")
    t0 = time.time()
    try:
        proc = subprocess.run(
            [PYTHON, path], cwd=HOST_PC, env=env,
            capture_output=True, text=True, timeout=timeout)
        dt = time.time() - t0
        ok = proc.returncode == 0
        marker = "PASS" if ok else "FAIL"
        print(f"[{marker}] {name} ({dt:.0f}s) - {desc}")
        if not ok:
            tail = (proc.stdout or "")[-1500:] + (proc.stderr or "")[-1500:]
            print("      输出尾部:\n" + "\n".join(
                "      " + l for l in tail.splitlines()[-12:]))
        return ok
    except subprocess.TimeoutExpired:
        print(f"[TIMEOUT] {name} ({timeout}s 超时) - {desc}")
        return False
    except Exception as e:
        print(f"[ERROR] {name} - {e}")
        return False


def main():
    args = sys.argv[1:]
    if "--list" in args:
        for name, desc, _t in TESTS:
            print(f"  {name:<40} {desc}")
        return 0

    subset = [a for a in args if not a.startswith("-")]
    targets = TESTS
    if subset:
        targets = [t for t in TESTS if any(s in t[0] for s in subset)]
        if not targets:
            print(f"未匹配到测试: {subset}")
            return 2

    print(f"=== 上位机回归测试（{len(targets)} 项）===")
    print(f"Python: {PYTHON}\n")
    results = []
    for name, desc, timeout in targets:
        results.append((name, run_one(name, desc, timeout)))

    passed = sum(1 for _, ok in results if ok)
    failed = len(results) - passed
    print(f"\n=== 结果: {passed}/{len(results)} 通过 ===")
    if failed:
        print("失败项:")
        for name, ok in results:
            if not ok:
                print(f"  ✗ {name}")
        return 1
    print("全部通过 ✅")
    return 0


if __name__ == "__main__":
    sys.exit(main())

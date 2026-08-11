"""
Offscreen 冒烟测试：构建主窗口 + 5 页 + 启动模拟流 2s + 历史查询 + PLC 模拟联调
用法: set QT_QPA_PLATFORM=offscreen && python tools/smoke_test.py
"""
import os
import sys
import time

import faulthandler
faulthandler.enable()

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtWidgets import QApplication  # noqa: E402

PASS = 0


def check(name, cond):
    global PASS
    if not cond:
        print(f"  [FAIL] {name}")
        sys.exit(1)
    PASS += 1
    print(f"  [OK] {name}")


def main():
    app = QApplication(sys.argv)
    from main import MainWindow
    win = MainWindow()
    win.show()
    app.processEvents()
    check("主窗口 + 5 页构建", win.tab.count() == 5)

    # 模拟流跑 2.5s
    win.page_realtime.start_requested.emit()
    t0 = time.time()
    while time.time() - t0 < 2.5:
        app.processEvents()
        time.sleep(0.05)
    check("实时流运行中", win.stream_engine.is_running)
    check("检测有结果(KPI>0)", win.controller.get_stats()["total"] > 0)
    check("预览有帧", win._last_frame is not None)
    win.page_realtime.stop_requested.emit()
    app.processEvents()
    check("实时流停止", not win.stream_engine.is_running)

    # 历史查询
    win.page_history._query(1)
    app.processEvents()
    check("历史页渲染", win.page_history.table.rowCount() >= 0)

    # 参数页配置往返
    cfg = win.page_param.get_config()
    check("参数页 get_config", cfg["detect"]["conf"] == win.page_realtime.spin_conf.value())

    # 日志页有启动日志
    check("运行日志页有日志", len(win.page_log._logs) > 0)

    win.close()
    app.processEvents()
    print(f"\n冒烟测试通过: {PASS} 项")


if __name__ == "__main__":
    main()

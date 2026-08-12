# -*- coding: utf-8 -*-
"""验证：无图/无连接点「开始检测」绝不启动模拟流；本地模型异步推理链路正常。
用法: set QT_QPA_PLATFORM=offscreen && python tools/test_no_sim_start.py
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtWidgets import QApplication  # noqa: E402

MODEL = r"D:\RK3568&Orin Nano\ORIN NANO\Model Training\runs\neu_yolov8s_e1003\weights\best.onnx"
IMG = r"D:\RK3568&Orin Nano\ORIN NANO\Model Training\datasets\Merged_SDX_NEU\train\images\bs_01.jpg"

PASS = 0


def check(name, cond):
    global PASS
    if not cond:
        print(f"  [FAIL] {name}")
        sys.exit(1)
    PASS += 1
    print(f"  [OK] {name}")


def pump(app, sec=0.1):
    t0 = time.time()
    while time.time() - t0 < sec:
        app.processEvents()
        time.sleep(0.01)


def main():
    app = QApplication(sys.argv)
    from main import MainWindow
    win = MainWindow()
    win.show()
    pump(app)

    # ---- 场景1：无图、无连接，直接点开始检测 → 不得启动任何流 ----
    win.controller.disconnect_tcp()
    pump(app)
    win._nano_active = False
    win._local_image = None
    win._local_image_active = False
    win.page_realtime.start_requested.emit()
    pump(app, 0.5)
    check("场景1: 实时流未启动", not win.stream_engine.is_running)
    check("场景1: 无检测结果", win.controller.get_stats()["total"] == 0)
    print("  [INFO] 场景1 应弹出提示「请选择本地图片或连接下位机」（offscreen 下不弹）")

    # ---- 场景2：有本地模型 + 已加载本地图片 → 异步推理出结果 ----
    win._local_model = MODEL
    import cv2
    import numpy as np
    raw = np.fromfile(IMG, dtype=np.uint8)
    img = cv2.imdecode(raw, cv2.IMREAD_COLOR)
    check("场景2: 测试图可读", img is not None)
    win._local_image = img
    win._local_image_path = IMG
    win._local_image_active = True
    win.page_realtime.update_image(__import__("PyQt5.QtGui", fromlist=["QImage"]).QImage(
        cv2.cvtColor(img, cv2.COLOR_BGR2RGB).data,
        img.shape[1], img.shape[0], img.shape[2] * img.shape[1],
        __import__("PyQt5.QtGui", fromlist=["QImage"]).QImage.Format_RGB888).copy())
    win.page_realtime.start_requested.emit()
    # 异步推理：等待结果信号（最多 20s）
    deadline = time.time() + 20
    while time.time() < deadline:
        pump(app, 0.05)
        if win.controller.get_stats()["total"] > 0:
            break
    check("场景2: 异步推理出结果", win.controller.get_stats()["total"] > 0)
    print(f"  [INFO] 场景2 检测总数={win.controller.get_stats()['total']}，"
          f"NG={win.controller.get_stats()['defect']}")

    # ---- 场景3：有图但无模型 → 明确提示且不崩溃 ----
    win._local_model = ""
    win.page_realtime.start_requested.emit()
    pump(app, 0.5)
    check("场景3: 无模型时无新结果入管线",
          win.controller.get_stats()["total"] == 1)  # 仍是场景2那 1 条

    # ---- 场景4：默认 infer_mode 必须是 none/tcp，禁止 sim ----
    check("场景4: 默认 infer_mode 非 sim", win.stream_engine.infer_mode != "sim")

    win.close()
    pump(app)
    print(f"\n测试通过: {PASS} 项")


if __name__ == "__main__":
    main()

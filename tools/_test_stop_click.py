# -*- coding: utf-8 -*-
"""按钮级端到端测试：真实点击「开始检测」按钮 → 推理 → 真实点击「停止检测」按钮 → 验证框清空
用法: QT_QPA_PLATFORM=offscreen python tools/_test_stop_click.py
"""
import os
import sys
import time

import numpy as np

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtWidgets import QApplication

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import main

MODEL = r"D:/RK3568&Orin Nano/ORIN NANO/Model Training/_archive_20260811/models/production/MVIT_连铸坯_v8s_mAP50_0.830_6类.pt"
IMAGE = r"D:/RK3568&Orin Nano/ORIN NANO/Model Training/_archive_20260811/datasets/MVIT_3子集_1810张_真实产线/ready/casting_billet/images/train/Co_40.jpg"


def pump(app, seconds):
    t0 = time.time()
    while time.time() - t0 < seconds:
        app.processEvents()
        time.sleep(0.01)


def main_test():
    app = QApplication([])
    w = main.MainWindow()
    w.show()
    pump(app, 0.3)

    import cv2
    raw = np.fromfile(IMAGE, dtype=np.uint8)
    img = cv2.imdecode(raw, cv2.IMREAD_COLOR)
    w._local_image = img
    w._local_image_active = True
    w._local_image_path = IMAGE
    w._local_model = MODEL
    rgb = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
    h, wdt, ch = rgb.shape
    from PyQt5.QtGui import QImage
    w.page_realtime.update_image(
        QImage(rgb.data, wdt, h, ch * wdt, QImage.Format_RGB888).copy())
    pump(app, 0.2)

    # 真实点击「开始检测」按钮
    print("[1] click btn_start")
    w.page_realtime.btn_start.click()
    pump(app, 0.5)

    # 等推理完成
    t0 = time.time()
    while time.time() - t0 < 30:
        pump(app, 0.1)
        eng = getattr(w, "_local_infer_engine", None)
        if eng is not None and not getattr(eng, "busy", False):
            break
    pump(app, 0.3)
    n_before = len(w.page_realtime.preview._dets)
    print(f"[2] inference done, dets={n_before}")
    assert n_before > 0, "无检测框, 无法验证"

    # 真实点击「停止检测」按钮
    print("[3] click btn_stop")
    w.page_realtime.btn_stop.click()
    pump(app, 0.3)
    n_after = len(w.page_realtime.preview._dets)
    print(f"[4] after stop dets={n_after} paused={getattr(w,'_detection_paused',None)}")
    pump(app, 1.0)
    n_final = len(w.page_realtime.preview._dets)
    print(f"[5] 1s later dets={n_final}")

    ok = n_after == 0 and n_final == 0
    print("[RESULT]", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main_test())

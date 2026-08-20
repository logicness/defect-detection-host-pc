# -*- coding: utf-8 -*-
"""真实端到端测试：开始检测→推理完成画框→停止检测→确认框被清除
用法: QT_QPA_PLATFORM=offscreen python tools/_test_stop_clear.py
"""
import os
import sys
import time

import numpy as np

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtWidgets import QApplication
from PyQt5.QtGui import QImage

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# ===== 数据隔离：测试不得写入生产 DB/配置/NG 图 =====
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools"))
import isolate_test_data as _iso  # noqa: E402
_TMP = _iso.setup()

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
    _iso.isolate_ng_saver(w)
    w.show()
    pump(app, 0.3)

    assert os.path.isfile(MODEL), f"模型不存在: {MODEL}"
    assert os.path.isfile(IMAGE), f"图片不存在: {IMAGE}"

    # ---- 模拟选图 ----
    img = cv2_imread(IMAGE)
    w._local_image = img
    w._local_image_active = True
    w._local_image_path = IMAGE
    w._local_model = MODEL
    rgb = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
    h, wdt, ch = rgb.shape
    w.page_realtime.update_image(
        QImage(rgb.data, wdt, h, ch * wdt, QImage.Format_RGB888).copy())
    pump(app, 0.2)

    # ---- 模拟点击「开始检测」 ----
    print("[1] 点击开始检测")
    w._on_start()
    pump(app, 0.5)

    # 等待推理完成（最多 30s）
    eng = getattr(w, "_local_infer_engine", None)
    t0 = time.time()
    while time.time() - t0 < 30:
        pump(app, 0.1)
        if eng is None:
            eng = getattr(w, "_local_infer_engine", None)
            continue
        if not getattr(eng, "busy", False):
            break
    pump(app, 0.3)
    dets_before = list(w.page_realtime.preview._dets)
    print(f"[2] 推理完成, 检测框数量 = {len(dets_before)}")
    assert len(dets_before) > 0, "推理完成后没有检测框, 无法验证停止清框"
    print("    类别:", [d[0] for d in dets_before])

    # 抓图确认框已画上
    w.page_realtime.preview.grab().save("tools/_shot_before.png")

    # ---- 模拟点击「停止检测」 ----
    print("[3] 点击停止检测")
    w._on_stop()
    pump(app, 0.3)
    dets_after = list(w.page_realtime.preview._dets)
    print(f"[4] 停止后检测框数量 = {len(dets_after)}")
    print(f"    _detection_paused = {getattr(w, '_detection_paused', None)}")

    # 等待 1s 覆盖延迟安全清框 + 可能的延迟信号
    pump(app, 1.0)
    dets_final = list(w.page_realtime.preview._dets)
    print(f"[5] 1s 后检测框数量 = {len(dets_final)}")
    w.page_realtime.preview.grab().save("tools/_shot_after.png")

    ok = len(dets_after) == 0 and len(dets_final) == 0
    print("[RESULT]", "PASS" if ok else "FAIL", f"(after={len(dets_after)} final={len(dets_final)})")
    return 0 if ok else 1


def cv2_imread(path):
    import cv2
    raw = np.fromfile(path, dtype=np.uint8)
    return cv2.imdecode(raw, cv2.IMREAD_COLOR)


if __name__ == "__main__":
    import cv2  # noqa
    sys.exit(main_test())

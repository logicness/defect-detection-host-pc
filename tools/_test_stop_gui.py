# -*- coding: utf-8 -*-
"""真实窗口自动化测试：QTimer 模拟点击开始/停止按钮，结果写入文件
用 pythonw 启动（脱离沙箱），模拟用户直接运行 main.py 的环境
"""
import os
import sys
import time

import numpy as np

RESULT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_gui_test_result.txt")

def log(msg):
    with open(RESULT, "a", encoding="utf-8") as f:
        f.write(f"[{time.strftime('%H:%M:%S')}] {msg}\n")

MODEL = r"D:/RK3568&Orin Nano/ORIN NANO/Model Training/_archive_20260811/models/production/MVIT_连铸坯_v8s_mAP50_0.830_6类.pt"
IMAGE = r"D:/RK3568&Orin Nano/ORIN NANO/Model Training/_archive_20260811/datasets/MVIT_3子集_1810张_真实产线/ready/casting_billet/images/train/Co_40.jpg"

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PyQt5.QtWidgets import QApplication
from PyQt5.QtCore import QTimer
from PyQt5.QtGui import QImage
import cv2
import main

app = None
w = None


def step1():
    raw = np.fromfile(IMAGE, dtype=np.uint8)
    img = cv2.imdecode(raw, cv2.IMREAD_COLOR)
    w._local_image = img
    w._local_image_active = True
    w._local_image_path = IMAGE
    w._local_model = MODEL
    rgb = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
    h, wt, ch = rgb.shape
    w.page_realtime.update_image(
        QImage(rgb.data, wt, h, ch * wt, QImage.Format_RGB888).copy())
    log("step1 图片加载完成")
    QTimer.singleShot(300, step2)


def step2():
    log("step2 点击「开始检测」按钮")
    w.page_realtime.btn_start.click()
    QTimer.singleShot(500, check_done)


def check_done():
    eng = getattr(w, "_local_infer_engine", None)
    if eng is not None and not getattr(eng, "busy", False):
        n = len(w.page_realtime.preview._dets)
        log(f"step3 推理完成 dets={n}")
        if n > 0:
            w.page_realtime.preview.grab().save(
                os.path.join(os.path.dirname(RESULT), "_gui_before.png"))
            QTimer.singleShot(200, step4)
        else:
            log("[FAIL] 无检测框")
            app.quit()
    else:
        QTimer.singleShot(300, check_done)


def step4():
    log("step4 点击「停止检测」按钮")
    w.page_realtime.btn_stop.click()
    QTimer.singleShot(500, check_clear)


def check_clear():
    n = len(w.page_realtime.preview._dets)
    log(f"step5 停止后 dets={n} paused={getattr(w, '_detection_paused', None)}")
    w.page_realtime.preview.grab().save(
        os.path.join(os.path.dirname(RESULT), "_gui_after.png"))
    log("[RESULT] " + ("PASS" if n == 0 else "FAIL"))
    QTimer.singleShot(300, app.quit)


def run():
    global app, w
    open(RESULT, "w", encoding="utf-8").close()
    log("启动真实窗口测试")
    try:
        app = QApplication(sys.argv)
        log("QApplication created")
        w = main.MainWindow()
        log("MainWindow created")
        w.show()
        log(f"MainWindow shown, isVisible={w.isVisible()}")
    except Exception:
        import traceback
        log("[EXCEPTION] " + traceback.format_exc())
        raise
    QTimer.singleShot(500, step1)
    sys.exit(app.exec_())


if __name__ == "__main__":
    try:
        run()
    except Exception:
        import traceback
        log("[OUTER_EXCEPTION] " + traceback.format_exc())
        raise

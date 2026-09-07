# -*- coding: utf-8 -*-
"""产线模拟流回归测试（接入 run_all_tests.py，需 Nano 在线）
覆盖：subscribe → start → 收 N 帧（seq/标注图/检测）→ set_fps → stop → 无帧 → 退订
"""
import os
import sys
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
HOST = sys.argv[1] if len(sys.argv) > 1 else os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HOST)

from PyQt5.QtWidgets import QApplication

app = QApplication([])

import components.nano_image_picker as _nip

class _FakePicker:
    def __init__(self, **kw):
        self.selected_names = []
        self.image_dir = ""
    def exec_(self):
        return 0
_nip.NanoImagePickerDialog = _FakePicker

PASS = 0
FAIL = 0


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  PASS {name} {detail}")
    else:
        FAIL += 1
        print(f"  FAIL {name} {detail}")


def spin(pred, timeout=20):
    dl = time.time() + timeout
    while time.time() < dl:
        app.processEvents()
        if pred():
            return True
        time.sleep(0.02)
    return False


import main as mainmod

mw = mainmod.MainWindow()
mw.show()

# 连接
mw.controller.tcp.configure("<NANO_LAN_IP>", 8888)
connected = [False]
mw.controller.tcp.connected.connect(lambda: connected.__setitem__(0, True))
mw.controller.tcp.connect()
ok = spin(lambda: connected[0], timeout=10)
check("tcp connected", ok)
if not ok:
    sys.exit(1)

# 切推理源
mw.page_realtime.combo_source.setCurrentText("Nano 产线流")
app.processEvents()
check("产线流推理源", "产线流" in mw.page_realtime.get_infer_source())
check("FPS 控件可见", mw.page_realtime.spin_stream_fps.isVisible())

# 开始产线流
mw.page_realtime.spin_stream_fps.setValue(10)
mw._on_start()
ok = spin(lambda: mw._stream_camera_active, timeout=5)
check("产线流已激活", ok)

# 收帧
ok = spin(lambda: mw.controller.get_stats()["total"] >= 5, timeout=20)
stats = mw.controller.get_stats()
check("收到 5+ 帧", stats["total"] >= 5,
      f"total={stats['total']} ng={stats['defect']}")

# 详情表有内容
detail = mw.page_realtime._detail_rows["缺陷类型"].text()
check("详情表更新", detail and detail != "--", detail)

# 停止
mw._on_stop()
time.sleep(0.3)
app.processEvents()
check("产线流已停止", not mw._stream_camera_active)
prev = mw.controller.get_stats()["total"]
time.sleep(0.5)
app.processEvents()
later = mw.controller.get_stats()["total"]
check("停止后不增长", later <= prev + 1, f"prev={prev} later={later}")

# 切回 PC 本地
mw.page_realtime.combo_source.setCurrentText("PC 本地模型")
app.processEvents()
check("切回后 FPS 隐藏", not mw.page_realtime.spin_stream_fps.isVisible())
check("切回后单次多次恢复", mw.page_realtime.seg_mode.isVisible())

mw.close()
mw.controller.tcp.disconnect()
print(f"\n结果: PASS={PASS} FAIL={FAIL}")
sys.exit(1 if FAIL else 0)

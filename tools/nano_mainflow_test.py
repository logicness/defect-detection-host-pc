# -*- coding: utf-8 -*-
"""上位机「下位机图片」交互 全流程回归测试（需 Nano 在线 <NANO_LAN_IP>）
用法（拷入 Host PC/tools/ 后）:
    python tools/nano_mainflow_test.py          # 默认仓库根（本文件上一级）
    python tools/nano_mainflow_test.py <DIR>    # 指定 Host PC 根目录
覆盖：单次单选→预览→单帧检测→重复检测→多次多选→批量→导航→停止→挂起自动检测。
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

FAKE = {"names": ["neu_pitted_surface_01.jpg"],
        "dir": "/home/nvidia/defect_detection/images/input"}


class FakePicker:
    def __init__(self, **kwargs):
        self.selected_names = list(FAKE["names"])
        self.image_dir = FAKE["dir"]

    def exec_(self):
        return 1


_nip.NanoImagePickerDialog = FakePicker

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

mw.controller.tcp.configure("<NANO_LAN_IP>", 8888)
connected = [False]
mw.controller.tcp.connected.connect(lambda: connected.__setitem__(0, True))
mw.controller.tcp.connect()
ok = spin(lambda: connected[0], timeout=10)
check("tcp connected", ok)
if not ok:
    sys.exit(1)

IMG0 = "neu_pitted_surface_01.jpg"   # 高置信麻点缺陷
IMG1 = "neu_inclusion_01.jpg"        # 多目标夹杂缺陷
N0 = lambda: f"nano://{FAKE['dir']}/{IMG0}"
N1 = lambda: f"nano://{FAKE['dir']}/{IMG1}"

print(f"[1] 单次模式：选择 {IMG0} → 主界面预览")
assert mw.page_realtime.get_detect_mode() == "单次检测"
mw._on_nano_image_detect()
ok = spin(lambda: mw._local_image is not None and mw._nano_preview_name == IMG0)
check("预览加载", ok and mw._nano_image_active and len(mw._multi_image_paths) == 1,
      mw._nano_preview_name)

print("[2] 单帧检测（主界面开始检测）")
mw._on_start()
ok = spin(lambda: N0() in mw._multi_results and not mw._batch_running, timeout=30)
check("单帧检测完成", ok, f"dets={len(mw._multi_results.get(N0(), {}).get('dets', []))}")
check("结果入 KPI", mw.controller.get_stats()["total"] >= 1,
      f"total={mw.controller.get_stats()['total']}")

print("[3] 再次检测同一张（重复检测）")
prev_total = mw.controller.get_stats()["total"]
mw._on_start()
ok = spin(lambda: mw.controller.get_stats()["total"] >= prev_total + 1, timeout=30)
check("重复检测", ok, f"total={mw.controller.get_stats()['total']}")

print("[4] 多次模式：选择 2 张 → 批量检测")
mw.page_realtime.seg_mode.set_current("多次检测")
FAKE["names"] = [IMG0, IMG1]
mw._on_nano_image_detect()
ok = spin(lambda: len(mw._multi_image_paths) == 2 and mw._local_image is not None)
check("多选加载", ok, f"paths={len(mw._multi_image_paths)}")
mw._on_start()
ok = spin(lambda: not mw._batch_running and len(mw._multi_results) >= 2, timeout=40)
check("批量检测完成", ok, f"results={len(mw._multi_results)}")
check("两张都有结果", N0() in mw._multi_results and N1() in mw._multi_results)

print("[5] 多图导航（上一张/下一张拉预览 + 详情跟随变化）")
mw._show_multi_image(1)
ok = spin(lambda: mw._nano_preview_name == IMG1)
check("导航到第 2 张", ok, mw._nano_preview_name)
# 第 2 张 neu_inclusion_01.jpg 应检出多个 inclusion → 详情缺陷类型应含 ×数量
detail2 = mw.page_realtime._detail_rows["缺陷类型"].text()
check("详情随翻页更新(含×数量)", ok and "×" in detail2 and "Inclusion" in detail2,
      detail2)
mw._show_multi_image(0)
ok = spin(lambda: mw._nano_preview_name == IMG0)
check("导航回第 1 张", ok, mw._nano_preview_name)
detail0 = mw.page_realtime._detail_rows["缺陷类型"].text()
check("详情随翻页变化", detail0 != detail2, detail0)

print("[6] 停止（清框复位，不崩溃）")
mw._on_stop()
check("停止后无批量", not mw._batch_running)
check("挂起标志已清", mw._nano_file_pending == "" and not mw._nano_wait_preview_detect)

print("[7] 预览未就绪时点开始检测（挂起→自动触发）")
mw._on_stop()
mw._local_image = None
mw._nano_preview_name = ""
FAKE["names"] = [IMG0]
mw._on_nano_image_detect()
mw._on_start()
ok = spin(lambda: mw._local_image is not None and N0() in mw._multi_results, timeout=30)
check("挂起自动检测", ok)

mw.close()
mw.controller.tcp.disconnect()
print(f"\n结果: PASS={PASS} FAIL={FAIL}")
sys.exit(1 if FAIL else 0)

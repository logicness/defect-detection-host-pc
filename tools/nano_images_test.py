# -*- coding: utf-8 -*-
"""真实 NanoImageDetectDialog × 真实板子 offscreen 联调
驱动完整对话框逻辑：连板 → 打开对话框 → 列表加载 → 选图预览 → 开始检测 →
结果提交（result_committed）→ 停止。验证 GUI 层（信号/缩略图/状态切换）。
"""
import os
import sys
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
HOST_PC = r"D:\RK3568&Orin Nano\ORIN NANO\Host PC"
sys.path.insert(0, HOST_PC)

from PyQt5.QtWidgets import QApplication
from core.tcp_client import TCPClient

app = QApplication([])

t = TCPClient()
t.configure("<NANO_LAN_IP>", 8888)
connected = [False]
t.connected.connect(lambda: connected.__setitem__(0, True))

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


def spin(pred, timeout=15):
    dl = time.time() + timeout
    while time.time() < dl:
        app.processEvents()
        if pred():
            return True
        time.sleep(0.02)
    return False


print("[1] 连接板子")
t.connect()
ok = spin(lambda: connected[0])
check("connected", ok)
if not ok:
    sys.exit(1)

print("[2] 打开对话框（真实 TCP + 列表加载）")
from components.nano_image_detect import NanoImageDetectDialog
committed = []
dlg = NanoImageDetectDialog(tcp_client=t, nano_model_name="Universal_Metal_35c")
dlg.result_committed.connect(lambda d: committed.append(d))
dlg.show()
ok = spin(lambda: dlg.list_images.count() >= 2, timeout=10)
check("缩略图列表加载", ok, f"count={dlg.list_images.count()}")
check("目录显示", dlg.image_dir.endswith("images/input"), dlg.image_dir)
check("模型灯", "Universal_Metal_35c" in dlg.light_model.txt.text(), dlg.light_model.txt.text())

print("[3] 选图预览（懒加载大图）")
dlg.list_images.item(0).setSelected(True)
ok = spin(lambda: dlg._frame is not None, timeout=10)
check("预览大图已加载", ok and dlg._frame is not None)

print("[4] 开始检测（清空选中 → 全部图片 × 2 轮）")
dlg.list_images.clearSelection()      # 验证「未选中 → 全部」路径
dlg.spin_rounds.setValue(2)
dlg._on_start()
ok = spin(lambda: not dlg._batch_running and len(committed) > 0, timeout=30)
check("批量完成", ok, f"committed={len(committed)}")
check("结果逐张提交", len(committed) == dlg._total_count == 4,
      f"{len(committed)}/{dlg._total_count}")
check("结果带标注", all(d.get("frame") is not None for d in committed))
check("结果路径 nano://", all(d.get("image_path", "").startswith("nano://") for d in committed))
if committed:
    ok_cnt = sum(1 for d in committed if not d["dets"])
    ng_cnt = len(committed) - ok_cnt
    print(f"  汇总: OK={ok_cnt} NG={ng_cnt} 总={len(committed)}")
    check("OK/NG 合计", ok_cnt + ng_cnt == len(committed))

print("[5] 停止批量")
dlg.spin_rounds.setValue(3)
dlg._on_start()          # 新一轮（3 轮）
spin(lambda: dlg._batch_running, timeout=5)
time.sleep(0.2)
dlg._on_stop()
ok = spin(lambda: not dlg._batch_running, timeout=15)
check("停止后回到 idle", ok)
check("停止按钮已禁用", not dlg.btn_stop.isEnabled())

print("[6] 保存标注图（路径不可达时不应崩溃）")
dlg._dets = [("crack", 0.9, 1, 1, 10, 10)] if dlg._frame is not None else []
try:
    dlg._on_save()
    check("保存不崩溃", True)
except Exception as e:
    check("保存不崩溃", False, str(e))

dlg.close()
t.disconnect()
print(f"\n结果: PASS={PASS} FAIL={FAIL}")
sys.exit(1 if FAIL else 0)

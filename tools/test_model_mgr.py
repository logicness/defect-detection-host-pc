# -*- coding: utf-8 -*-
"""模型管理对话框 v4（单页三区）功能测试（offscreen，模型库重定向到临时文件）
覆盖：
- 质量评分高/中/低/未知
- 树形列表：本地库 + 下位机分组、选中即详情
- 添加到模型库（去重）/ 从库移除（不删文件）/ 删除信号
- 加载本地模型信号 / 重新打开恢复模型库
用法: set QT_QPA_PLATFORM=offscreen && python tools/test_model_mgr.py
"""
import os
import sys
import time
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtWidgets import QApplication  # noqa: E402
from PyQt5.QtCore import Qt  # noqa: E402

MODEL = r"D:\RK3568&Orin Nano\ORIN NANO\Model Training\runs\neu_yolov8s_e1003\weights\best.onnx"
TEST_MODEL = r"D:\RK3568&Orin Nano\ORIN NANO\Model Training\runs\neu_yolov8s_e1003\weights\best.pt"

PASS = 0


def check(name, cond):
    global PASS
    if not cond:
        print(f"  [FAIL] {name}")
        sys.exit(1)
    PASS += 1
    print(f"  [OK] {name}")


def pump(app, sec=0.05):
    t0 = time.time()
    while time.time() - t0 < sec:
        app.processEvents()
        time.sleep(0.01)


def tree_item_paths(tree):
    """遍历树,返回 [(text, path, source, in_lib), ...]"""
    out = []
    it = tree.invisibleRootItem()
    for i in range(it.childCount()):
        group = it.child(i)
        for j in range(group.childCount()):
            c = group.child(j)
            out.append((
                c.text(0),
                c.data(0, Qt.UserRole) or "",
                c.data(0, Qt.UserRole + 1) or "",
                bool(c.data(0, Qt.UserRole + 2)),
            ))
    return out


def main():
    app = QApplication(sys.argv)

    # 模型库重定向到临时文件（隔离测试，不污染 data/model_library.json）
    tmp_dir = tempfile.mkdtemp(prefix="mmgr_test_")
    tmp_lib = os.path.join(tmp_dir, "model_library.json")
    from components import model_library as mlib
    mlib._library_path = lambda: tmp_lib

    # ---- 质量评分单元测试 ----
    label, score, color, tip = mlib.score_quality(MODEL)
    check("best.onnx 质量为「高」且得分>=80", label == "高" and score >= 80)
    label2, score2, _, _ = mlib.score_quality(
        r"D:\models\yolo_last_mAP50_0.65.pt")
    check("mAP0.65 质量为「中」", label2 == "中" and 60 <= score2 < 80)
    label3, score3, _, _ = mlib.score_quality(
        r"D:\models\some_model_mAP50_0.45.pt")
    check("mAP0.45 质量为「低」", label3 == "低" and score3 < 60)
    label4, score4, _, _ = mlib.score_quality(r"D:\models\unknown.xyz")
    check("无信息模型质量为「未知」", label4 == "未知" and score4 == 0)

    # 假 QMessageBox：删除确认固定选「仅从库移除」，信息/警告静默
    from PyQt5.QtWidgets import QMessageBox as _RealMB

    class _FakeMB:
        Warning = _RealMB.Warning
        Information = _RealMB.Information
        DestructiveRole = _RealMB.DestructiveRole
        AcceptRole = _RealMB.AcceptRole

        def __init__(self, *a, **k):
            self._keep = object()
            self._del = object()
            self._clicked = self._keep

        def setWindowTitle(self, *a):
            pass

        def setIcon(self, *a):
            pass

        def setText(self, *a):
            pass

        def setInformativeText(self, *a):
            pass

        def addButton(self, text, role):
            return self._del if role == _RealMB.DestructiveRole else self._keep

        def setDefaultButton(self, *a):
            pass

        def exec_(self):
            return 0

        def clickedButton(self):
            return self._clicked

        @staticmethod
        def information(*a, **k):
            pass

        @staticmethod
        def warning(*a, **k):
            pass

    import components.model_manager_dialog as mmd
    mmd.QMessageBox = _FakeMB

    from components.model_manager_dialog import ModelManagerDialog
    from core.tcp_client import TCPClient

    # ---- 模型信息提取 ----
    from components.model_info import model_tags
    tags = model_tags(MODEL)
    check("best.onnx 识别为 NEU-DET 数据集", tags["dataset"] == "NEU-DET")
    check("best.onnx 类别数为 6", tags["classes"] == 6)
    check("best.onnx 被推荐", tags["is_recommended"])
    tags_yolo = model_tags(r"D:\\yolov8s.pt")
    check("yolov8s.pt 识别为预训练", tags_yolo["is_pretrained"])
    check("yolov8s.pt 不被推荐", not tags_yolo["is_recommended"])

    tcp = TCPClient()
    dlg = ModelManagerDialog(tcp, open_tab="manage")
    dlg.show()
    pump(app)

    check("对话框构建（单页三区）", dlg.tree is not None)
    check("加载/添加/删除按钮存在",
          dlg.btn_load is not None and dlg.btn_add_lib is not None
          and dlg.btn_del is not None)
    # 树形列表有「本地库」「下位机」两个分组
    it = dlg.tree.invisibleRootItem()
    groups = [it.child(i).text(0) for i in range(it.childCount())]
    check("树形列表含本地库/下位机分组",
          any("本地" in g for g in groups) and any("下位机" in g for g in groups))

    # ---- 从扫描结果添加进模型库（v1.4 流程：扫描 → 选中 → 添加到库）----
    dlg._scan_results = [MODEL]
    dlg._refresh_scan_panel()
    pump(app)
    check("扫描结果列表含 MODEL",
          any(dlg.scan_list.item(i).data(Qt.UserRole) == MODEL
              for i in range(dlg.scan_list.count())))
    # 模拟点击扫描项（选中 source=scan）
    for i in range(dlg.scan_list.count()):
        if dlg.scan_list.item(i).data(Qt.UserRole) == MODEL:
            dlg._on_scan_item_clicked(dlg.scan_list.item(i))
            break
    pump(app)
    check("选中扫描项后路径正确", dlg._selected_path == MODEL
          and dlg._selected_source == "scan")
    dlg._on_add_to_library()
    pump(app)
    lib = mlib.load_library()
    check("添加后模型库含该模型", any(e.get("path") == MODEL for e in lib))
    items = tree_item_paths(dlg.tree)
    matched = [t for t in items if t[1] == MODEL]
    check("列表项标记为 [已入库]",
          matched and any(t[3] for t in matched))

    # ---- 去重：重复添加不新增 ----
    before = len(mlib.load_library())
    dlg._on_add_to_library()
    pump(app)
    check("重复添加不新增条目", len(mlib.load_library()) == before)

    # ---- 删除（仅从库移除，不删文件）----
    before_file = os.path.isfile(MODEL)
    deleted = []
    dlg.local_model_deleted.connect(deleted.append)
    dlg._on_delete_local()
    pump(app)
    check("删除后模型库不含该模型",
          not any(e.get("path") == MODEL for e in mlib.load_library()))
    check("删除未删磁盘文件", os.path.isfile(MODEL) == before_file)
    check("删除信号已发出", deleted == [MODEL])

    # ---- 恢复：重新打开对话框列出模型库 ----
    mlib.save_library([{"path": TEST_MODEL, "name": os.path.basename(TEST_MODEL),
                        "size_mb": round(os.path.getsize(TEST_MODEL) / 1e6, 2)}])
    dlg2 = ModelManagerDialog(tcp, open_tab="manage")
    pump(app)
    items2 = tree_item_paths(dlg2.tree)
    check("重新打开列出模型库条目", any(t[1] == TEST_MODEL for t in items2))

    # ---- 加载信号：选中模型点「加载为本地推理模型」----
    dlg2._select_tree_item(TEST_MODEL)
    pump(app)
    got = []
    dlg2.local_model_selected.connect(got.append)
    dlg2._on_load_current()
    pump(app)
    check("加载信号发出正确路径", got == [TEST_MODEL])

    print(f"\n模型管理测试通过: {PASS} 项")


if __name__ == "__main__":
    main()

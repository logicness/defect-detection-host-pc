# -*- coding: utf-8 -*-
"""
本地图片检测对话框
==================
- 选择任意本地图片 → 本地推理（有本地模型走 onnx/pt 真实推理；无模型走模拟演示）
- 检测结果叠加显示（绿=ROI，红=缺陷框），支持保存标注图
- 结果经 result_committed 信号交给 main 统一入管线（KPI/写库）
"""
import os
import time
import random
from datetime import datetime

import cv2

from PyQt5.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QFileDialog,
    QMessageBox
)
from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtGui import QImage

from components.image_preview import ImagePreview
from components.common_widgets import Card, StatusLight

_SAVE_DIR = "D:/Inspect/LocalDetect"
_SIM_CLASSES = ["scratches", "crazing", "inclusion", "patches"]


def _sim_detect_on_image(frame) -> list:
    """无模型时的模拟演示：在图片上生成 1~2 个随机缺陷框（UI 格式）"""
    h, w = frame.shape[:2]
    n = random.randint(1, 2)
    dets = []
    for _ in range(n):
        bw = max(20, int(w * random.uniform(0.08, 0.16)))
        bh = max(16, int(h * random.uniform(0.06, 0.12)))
        x1 = random.randint(0, max(1, w - bw - 1))
        y1 = random.randint(0, max(1, h - bh - 1))
        cls = random.choice(_SIM_CLASSES)
        conf = round(random.uniform(0.82, 0.98), 2)
        dets.append((cls, conf, x1, y1, x1 + bw, y1 + bh))
    return dets


class LocalImageDetectDialog(QDialog):
    """本地图片检测对话框：选图 → 本地推理 → 显示结果"""

    result_committed = pyqtSignal(dict)   # {"dets": ui列表, "image_path": str, "frame": bgr}

    def __init__(self, local_model: str = "", rois: list = None, parent=None):
        super().__init__(parent)
        self._model = local_model or ""
        self._rois = rois or []
        self._img_path = ""
        self._frame = None          # BGR numpy
        self._dets = []             # UI 格式 [(cls, conf, x1,y1,x2,y2)]
        self._last_ms = 0.0
        self._infer = None          # LocalInferEngine（有模型时）
        if self._model:
            try:
                from core.local_infer import LocalInferEngine
                self._infer = LocalInferEngine(self)
                self._infer.result_ready.connect(self._on_infer_result)
                self._infer.error_ready.connect(self._on_infer_error)
            except Exception:
                self._infer = None

        self.setWindowTitle("本地图片检测")
        self.setMinimumSize(760, 620)
        self.resize(860, 680)
        self._build_ui()
        self._refresh_status()

    # ---------------- UI ----------------
    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(14, 12, 14, 12)
        root.setSpacing(10)

        # 顶部操作行
        top = QHBoxLayout()
        self.btn_pick = QPushButton("选择图片...")
        self.btn_pick.setObjectName("btnPrimary")
        self.btn_pick.clicked.connect(self._on_pick)
        self.btn_detect = QPushButton("开始检测")
        self.btn_detect.setEnabled(False)
        self.btn_detect.clicked.connect(self._on_detect)
        self.btn_save = QPushButton("保存标注图")
        self.btn_save.setEnabled(False)
        self.btn_save.clicked.connect(self._on_save)
        top.addWidget(self.btn_pick)
        top.addWidget(self.btn_detect)
        top.addWidget(self.btn_save)
        top.addStretch()
        self.light_model = StatusLight("未加载模型")
        top.addWidget(self.light_model)
        root.addLayout(top)

        # 预览
        card = Card("检测预览")
        self.preview = ImagePreview()
        self.preview.setMinimumHeight(420)
        card.body.addWidget(self.preview, 1)
        root.addWidget(card, 1)

        # 结果信息
        info = Card("检测结果")
        row = QHBoxLayout()
        self.lbl_result = QLabel("结果: --")
        self.lbl_defect = QLabel("缺陷: --")
        self.lbl_conf = QLabel("置信度: --")
        self.lbl_time = QLabel("耗时: --")
        for l in (self.lbl_result, self.lbl_defect, self.lbl_conf, self.lbl_time):
            l.setStyleSheet("color:#e2e8f0; font-size:15px; background:transparent;")
            row.addWidget(l)
            row.addStretch()
        info.body.addLayout(row)
        root.addWidget(info)

        # 关闭
        btns = QHBoxLayout()
        btns.addStretch()
        btn_close = QPushButton("关闭")
        btn_close.setFixedWidth(120)
        btn_close.clicked.connect(self.accept)
        btns.addWidget(btn_close)
        root.addLayout(btns)

    def _refresh_status(self):
        if self._model and os.path.isfile(self._model):
            self.light_model.set_status(1, f"模型: {os.path.basename(self._model)}")
        elif self._model:
            # 模型文件不存在 → 回退模拟演示
            self._model = ""
            self._infer = None
            self.light_model.set_status(2, "模型文件不存在，将使用模拟演示")
        else:
            self.light_model.set_status(2, "模拟演示模式（未设置本地模型）")

    # ---------------- 选图 ----------------
    def _on_pick(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "选择检测图片", "",
            "图片文件 (*.png *.jpg *.jpeg *.bmp)")
        if not path:
            return
        frame = cv2.imread(path)
        if frame is None:
            QMessageBox.warning(self, "本地图片检测", f"无法读取图片: {path}")
            return
        self._img_path = path
        self._frame = frame
        self._dets = []
        self._show_frame()
        self.preview.clear_detections()
        self.btn_detect.setEnabled(True)
        self.btn_save.setEnabled(False)
        self.lbl_result.setText(f"结果: 已加载 {os.path.basename(path)}")
        self.lbl_defect.setText("缺陷: --")
        self.lbl_conf.setText("置信度: --")
        self.lbl_time.setText("耗时: --")

    def _show_frame(self):
        rgb = cv2.cvtColor(self._frame, cv2.COLOR_BGR2RGB)
        h, w, ch = rgb.shape
        self.preview.set_image(
            QImage(rgb.data, w, h, ch * w, QImage.Format_RGB888).copy())
        self.preview.set_rois(self._rois)

    # ---------------- 检测 ----------------
    def _on_detect(self):
        if self._frame is None:
            return
        if self._infer is not None:
            # 真实本地推理（异步）
            self.lbl_result.setText("结果: 推理中...")
            self.lbl_time.setText("耗时: --")
            self.btn_detect.setEnabled(False)
            self._infer.detect(self._img_path, self._model)
        else:
            # 模拟演示（同步）
            t0 = time.perf_counter()
            dets = _sim_detect_on_image(self._frame)
            self._last_ms = (time.perf_counter() - t0) * 1000
            self._show_result(dets)

    def _on_infer_result(self, result: dict):
        self.btn_detect.setEnabled(True)
        raw = result.get("detections", [])
        from core.local_infer import NEU_CLASSES
        dets = []
        for d in raw:
            box = d.get("box", [0, 0, 0, 0])
            cid = d.get("class_id", 0)
            cls = NEU_CLASSES[cid] if cid < len(NEU_CLASSES) else f"cls{cid}"
            dets.append((cls, d.get("confidence", 0), *box[:4]))
        self._last_ms = result.get("timing", {}).get("total_ms", 0)
        self._show_result(dets)

    def _on_infer_error(self, msg: str):
        self.btn_detect.setEnabled(True)
        self.lbl_result.setText(f"结果: 推理失败")
        QMessageBox.warning(self, "本地图片检测", f"本地推理失败: {msg}")

    def _show_result(self, dets: list):
        self._dets = dets
        self.preview.set_detections(dets)
        has = len(dets) > 0
        self.lbl_result.setText(f"结果: {'NG 缺陷' if has else 'OK 合格'}")
        if has:
            top = max(dets, key=lambda d: d[1])
            self.lbl_defect.setText(f"缺陷: {top[0]} × {len(dets)}")
            self.lbl_conf.setText(f"置信度: {top[1]:.2f}")
        else:
            self.lbl_defect.setText("缺陷: 无")
            self.lbl_conf.setText("置信度: --")
        self.lbl_time.setText(f"耗时: {self._last_ms:.0f} ms")
        self.btn_save.setEnabled(True)
        # 交回主流程（KPI / 写库 / 历史）
        self.result_committed.emit(
            {"dets": list(dets), "image_path": self._img_path, "frame": self._frame})

    # ---------------- 保存标注图 ----------------
    def _on_save(self):
        if self._frame is None:
            return
        os.makedirs(_SAVE_DIR, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        out_path = os.path.join(_SAVE_DIR, f"local_{stamp}.png")
        img = self._frame.copy()
        for cls, conf, x1, y1, x2, y2 in self._dets:
            cv2.rectangle(img, (int(x1), int(y1)), (int(x2), int(y2)),
                          (0, 0, 255), 2)
            cv2.putText(img, f"{cls} {conf:.2f}", (int(x1), max(18, int(y1) - 6)),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 0, 255), 2)
        cv2.imwrite(out_path, img)
        # CSV 记录
        try:
            csv_path = os.path.join(_SAVE_DIR, "records.csv")
            new = not os.path.exists(csv_path)
            with open(csv_path, "a", encoding="utf-8-sig") as f:
                if new:
                    f.write("时间,图片,结果,缺陷数,耗时ms\n")
                f.write(f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')},"
                        f"{self._img_path},{'NG' if self._dets else 'OK'},"
                        f"{len(self._dets)},{self._last_ms:.0f}\n")
        except Exception:
            pass
        QMessageBox.information(self, "本地图片检测", f"标注图已保存:\n{out_path}")

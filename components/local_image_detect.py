# -*- coding: utf-8 -*-
"""
本地图片检测对话框（增强版，对齐 Nano 模型库联动）
=================================================
- 选择任意本地图片 → 本地推理（有本地模型走 onnx/pt 真实推理；无模型/Nano 未连接时明确提示）
- 模型选择：当前本地模型 / 我的模型库 / 浏览 .onnx .pt 文件（对话框内随时切换）
- 检测结果叠加显示（绿=ROI，红=缺陷框），支持保存标注图
- 结果经 result_committed 信号交给 main 统一入管线（KPI/写库）
"""
import os
import time
from datetime import datetime

import cv2

from PyQt5.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QFileDialog,
    QMessageBox, QComboBox, QWidget, QApplication
)
from PyQt5.QtCore import pyqtSignal, Qt
from PyQt5.QtGui import QImage

from components.image_preview import ImagePreview
from components.common_widgets import Card, StatusLight, FocusComboBox
from components import model_library as mlib

_SAVE_DIR = "D:/Inspect/LocalDetect"
_BROWSE_TAG = "__BROWSE__"
_NANO_TAG = "__NANO__"


class LocalImageDetectDialog(QDialog):
    """本地图片检测对话框：选图 → 选模型 → 本地推理 → 显示结果"""

    result_committed = pyqtSignal(dict)   # {"dets": ui列表, "image_path": str, "frame": bgr}

    def __init__(self, local_model: str = "", rois: list = None, parent=None,
                 initial_image: str = None, default_image_dir: str = "",
                 tcp_client=None):
        super().__init__(parent)
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.Dialog)
        self._tcp = tcp_client
        self._model = local_model or ""
        self._rois = rois or []
        self._default_image_dir = default_image_dir
        self._img_path = ""
        self._frame = None          # BGR numpy
        self._dets = []             # UI 格式 [(cls, conf, x1,y1,x2,y2)]
        self._last_ms = 0.0
        self._infer = None          # LocalInferEngine（有模型时）
        self._nano_pending = False  # 是否正在等 Nano 结果

        self.setWindowTitle("本地图片检测")
        self.setMinimumSize(800, 680)
        self.resize(960, 760)
        self._build_ui()
        self._populate_model_combo()
        self._set_model(self._model)
        if initial_image:
            self._load_image(initial_image)
            if hasattr(self.parent(), '_save_state') and callable(self.parent()._save_state):
                self.parent()._save_state(last_image_dir=os.path.dirname(initial_image) or "")

    # ---------------- UI ----------------
    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # 自定义标题栏（无边框窗口需手动实现拖动）
        title_bar = QWidget()
        title_bar.setFixedHeight(40)
        title_bar.setStyleSheet("background-color:#10151f; border-bottom:1px solid #1f2937;")
        tlay = QHBoxLayout(title_bar)
        tlay.setContentsMargins(16, 0, 12, 0)
        tlay.setSpacing(8)
        t_title = QLabel("本地图片检测")
        t_title.setStyleSheet("color:#f1f5f9; font-size:16px; font-weight:600; background:transparent;")
        tlay.addWidget(t_title)
        tlay.addStretch()
        t_close = QPushButton("✕")
        t_close.setFixedSize(36, 28)
        t_close.setStyleSheet(
            "QPushButton{border:none; background:transparent; color:#94a3b8; font-size:14px;}"
            "QPushButton:hover{background:#dc2626; color:#ffffff; border-radius:4px;}")
        t_close.clicked.connect(self.reject)
        tlay.addWidget(t_close)
        self._drag_pos = None  # 拖动用（绑定到对话框实例，见 _title_mouse_*）
        title_bar.mousePressEvent = self._title_mouse_press
        title_bar.mouseMoveEvent = self._title_mouse_move
        title_bar.mouseReleaseEvent = lambda e: setattr(self, '_drag_pos', None)
        root.addWidget(title_bar)

        # 内容区
        body = QWidget()
        body_layout = QVBoxLayout(body)
        body_layout.setContentsMargins(14, 12, 14, 12)
        body_layout.setSpacing(10)

        # 顶部操作行
        top = QHBoxLayout()
        self.btn_pick = QPushButton("选择图片...")
        self.btn_pick.setObjectName("btnPrimary")
        self.btn_pick.clicked.connect(self._on_pick)
        self.btn_detect = QPushButton("开始检测")
        self.btn_detect.setFixedHeight(40)
        self.btn_detect.setEnabled(False)
        self.btn_detect.clicked.connect(self._on_detect)
        self.btn_save = QPushButton("保存标注图")
        self.btn_save.setFixedHeight(40)
        self.btn_save.setEnabled(False)
        self.btn_save.clicked.connect(self._on_save)
        top.addWidget(self.btn_pick)
        top.addWidget(self.btn_detect)
        top.addWidget(self.btn_save)
        top.addStretch()
        self.light_model = StatusLight("未加载模型")
        top.addWidget(self.light_model)
        body_layout.addLayout(top)

        # 模型选择行
        mrow = QHBoxLayout()
        mrow.addWidget(QLabel("检测模型:"))
        self.combo_model = FocusComboBox()
        self.combo_model.setMinimumHeight(36)
        self.combo_model.setMinimumWidth(360)
        self.combo_model.currentIndexChanged.connect(self._on_model_changed)
        mrow.addWidget(self.combo_model, 1)
        mrow.addStretch()
        body_layout.addLayout(mrow)

        # 预览
        card = Card("检测预览")
        self.preview = ImagePreview()
        self.preview.setMinimumHeight(400)
        card.body.addWidget(self.preview, 1)
        body_layout.addWidget(card, 1)

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
        body_layout.addWidget(info)

        # 关闭
        btns = QHBoxLayout()
        btns.addStretch()
        btn_close = QPushButton("关闭")
        btn_close.setFixedWidth(120)
        btn_close.clicked.connect(self.accept)
        btns.addWidget(btn_close)
        body_layout.addLayout(btns)

        root.addWidget(body)

    # ---------------- 标题栏拖动 ----------------
    def _title_mouse_press(self, event):
        if event.button() == Qt.LeftButton:
            self._drag_pos = event.globalPos() - self.frameGeometry().topLeft()

    def _title_mouse_move(self, event):
        if hasattr(self, '_drag_pos') and self._drag_pos is not None:
            self.move(event.globalPos() - self._drag_pos)

    # ---------------- 模型选择 ----------------
    def _populate_model_combo(self):
        self.combo_model.blockSignals(True)
        self.combo_model.clear()
        # 条目格式：(显示文本, 模型路径 或 特殊标记)
        # 不再提供「模拟演示」选项；无模型时选择框第一项为提示项
        entries = [("请先选择本地模型", "")]
        if self._tcp and getattr(self._tcp, "is_connected", False):
            entries.append(("Nano 推理（下位机）", _NANO_TAG))
        if self._model and os.path.isfile(self._model):
            entries.append((f"当前模型: {os.path.basename(self._model)}",
                            self._model))
        for e in mlib.load_library():
            path = e.get("path", "")
            if path and os.path.isfile(path) and path not in [p for _, p in entries]:
                q_label, _ = mlib.infer_quality_from_path(path)
                entries.append((f"[{q_label}] {os.path.basename(path)}", path))
        entries.append(("——— 浏览模型文件... ———", _BROWSE_TAG))
        for text, data in entries:
            self.combo_model.addItem(text, data)
        self.combo_model.blockSignals(False)
        # 默认选中当前模型（若有）
        if self._model and os.path.isfile(self._model):
            idx = 0
            for i in range(self.combo_model.count()):
                if self.combo_model.itemData(i) == self._model:
                    idx = i
                    break
            self.combo_model.setCurrentIndex(idx)
        else:
            self.combo_model.setCurrentIndex(0)

    def _on_model_changed(self, index: int):
        data = self.combo_model.itemData(index)
        if data == _BROWSE_TAG:
            path, _ = QFileDialog.getOpenFileName(
                self, "选择本地模型", "", "模型文件 (*.onnx *.pt)")
            if not path:
                # 取消浏览 → 回到上一选择
                prev = self._model
                self._populate_model_combo()
                if prev and os.path.isfile(prev):
                    self._set_model(prev)
                return
            self._model = path
            self._populate_model_combo()  # 重新构建（新模型会进入「当前模型」项）
            self._set_model(path)
        elif data == _NANO_TAG:
            self._set_model(_NANO_TAG)
        else:
            self._set_model(data or "")

    def _set_model(self, path: str):
        """切换检测模型：重建推理引擎 + 刷新状态灯"""
        if self._infer is not None:
            try:
                self._infer.disconnect()
            except Exception:
                pass
            self._infer = None
        # 断开旧的 Nano 结果监听
        if getattr(self, "_nano_connected", False) and self._tcp is not None:
            try:
                self._tcp.result_received.disconnect(self._on_nano_result)
            except Exception:
                pass
            self._nano_connected = False

        self._model = path or ""
        if self._model == _NANO_TAG:
            if self._tcp is not None and getattr(self._tcp, "is_connected", False):
                self._tcp.result_received.connect(self._on_nano_result)
                self._nano_connected = True
                self.light_model.set_status(1, "Nano 推理模式")
            else:
                self.light_model.set_status(2, "Nano 未连接，请先连接或选择本地模型")
                self._model = ""
            return

        if self._model and os.path.isfile(self._model):
            try:
                from core.local_infer import LocalInferEngine
                self._infer = LocalInferEngine(self)
                self._infer.result_ready.connect(self._on_infer_result)
                self._infer.error_ready.connect(self._on_infer_error)
                self.light_model.set_status(
                    1, f"模型: {os.path.basename(self._model)}")
            except Exception as e:
                self._infer = None
                self.light_model.set_status(
                    2, f"模型加载失败: {e}")
        elif self._model:
            self._model = ""
            self.light_model.set_status(2, "模型文件不存在，请先选择模型")
        else:
            self.light_model.set_status(2, "未设置本地模型，检测前请先选择模型")

    # ---------------- 选图 ----------------
    def _on_pick(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "选择检测图片", self._default_image_dir,
            "图片文件 (*.png *.jpg *.jpeg *.bmp)")
        if path:
            self._load_image(path)
            # 通知调用方记忆目录
            if hasattr(self.parent(), '_save_state') and callable(self.parent()._save_state):
                self.parent()._save_state(last_image_dir=os.path.dirname(path) or "")

    def _load_image(self, path: str):
        """加载图片并重置检测状态（入口自动加载时也会调用）"""
        # cv2.imread 不支持中文路径，用 np.fromfile + imdecode 替代
        import numpy as np
        try:
            raw = np.fromfile(path, dtype=np.uint8)
            frame = cv2.imdecode(raw, cv2.IMREAD_COLOR)
        except Exception:
            frame = None
        if frame is None:
            if QApplication.platformName() != "offscreen":  # 无头环境不弹模态框
                QMessageBox.warning(self, "本地图片检测", f"无法读取图片: {path}")
            return False
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
        return True

    def _show_frame(self):
        rgb = cv2.cvtColor(self._frame, cv2.COLOR_BGR2RGB)
        h, w, ch = rgb.shape
        self.preview.set_image(
            QImage(rgb.data, w, h, ch * w, QImage.Format_RGB888).copy())
        self.preview.set_rois(self._rois)

    # ---------------- 检测 ----------------
    def _on_detect(self):
        if self._frame is None:
            self.lbl_result.setText("结果: 未选择图片")
            if QApplication.platformName() != "offscreen":
                QMessageBox.information(
                    self, "本地图片检测",
                    "当前未选择图片，请先点击「选择图片...」加载要检测的图片。")
            return
        if self._model == _NANO_TAG and self._tcp is not None and self._tcp.is_connected:
            # Nano 推理：发送图片到下位机，结果异步回传
            self._nano_pending = True
            self.lbl_result.setText("结果: Nano 推理中...")
            self.lbl_time.setText("耗时: --")
            self.btn_detect.setEnabled(False)
            ok, buf = cv2.imencode(".jpg", self._frame)
            if ok:
                self._tcp.send_image(buf.tobytes())
            else:
                self._nano_pending = False
                self.btn_detect.setEnabled(True)
                self.lbl_result.setText("结果: 编码失败")
            return
        if self._infer is not None:
            # 真实本地推理（异步）
            self.lbl_result.setText("结果: 推理中...")
            self.lbl_time.setText("耗时: --")
            self.btn_detect.setEnabled(False)
            self._infer.detect(self._img_path, self._model)
        else:
            # 无可用模型：明确提示，不再模拟演示
            self.lbl_result.setText("结果: 未选择模型")
            if QApplication.platformName() != "offscreen":
                QMessageBox.information(
                    self, "本地图片检测",
                    "当前未选择可用的本地模型。\n\n"
                    "请先选择 .onnx / .pt 模型，或选择 Nano 推理模式后再点击检测。")

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
        self.lbl_result.setText("结果: 推理失败")
        QMessageBox.warning(self, "本地图片检测", f"本地推理失败: {msg}")

    def _on_nano_result(self, result: dict):
        """Nano 回传结果（TCP detect_response 原始格式）"""
        if not self._nano_pending:
            return
        self._nano_pending = False
        self.btn_detect.setEnabled(True)
        from core.controller import NEU_CLASSES
        dets = []
        for det in result.get("detections", []):
            box = det.get("box", [0, 0, 0, 0])
            cid = det.get("class_id", 0)
            cls = NEU_CLASSES[cid] if cid < len(NEU_CLASSES) else f"cls{cid}"
            dets.append((cls, det.get("confidence", 0), *box[:4]))
        self._last_ms = result.get("inference_ms", 0) or result.get("timing", {}).get("total_ms", 0)
        self._show_result(dets)

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
                    f.write("时间,图片,模型,结果,缺陷数,耗时ms\n")
                model_name = os.path.basename(self._model) if self._model else "未选择模型"
                f.write(f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')},"
                        f"{self._img_path},{model_name},"
                        f"{'NG' if self._dets else 'OK'},"
                        f"{len(self._dets)},{self._last_ms:.0f}\n")
        except Exception:
            pass
        QMessageBox.information(self, "本地图片检测", f"标注图已保存:\n{out_path}")

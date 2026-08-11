# -*- coding: utf-8 -*-
"""
ROI 可视化编辑对话框
====================
- 坐标表单：名称 / 启用 / x / y / w / h
- 可视化画布：图像背景 + 绿色 ROI 框，支持鼠标拖动整体移动、拖右下角手柄缩放
- 坐标系：ROI 使用图像像素坐标（与检测管线一致）；
  传入 QImage 时画布等比铺入图像，所见即所得；无图像时以 640x480 为默认参考画布
"""
from PyQt5.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel, QLineEdit,
    QPushButton, QCheckBox, QFrame
)
from PyQt5.QtCore import Qt, QRect
from PyQt5.QtGui import QImage, QPixmap, QPainter, QColor, QPen, QFont

CANVAS_W, CANVAS_H = 560, 380
_MARGIN = 14


def _spin(min_v, max_v, val):
    s = SpinBox()
    s.setRange(min_v, max_v)
    s.setValue(val)
    s.setFixedHeight(34)
    return s


class RoiCanvas(QFrame):
    """ROI 可视化画布：图像背景 + 绿色 ROI 框（可拖动移动/缩放）

    坐标系约定：ROI 为图像像素坐标；有图像时画布 = 图像等比缩放，
    无图像时使用 640x480 默认画布（与模拟帧一致）。
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(CANVAS_W, CANVAS_H)
        self.setStyleSheet(
            "QFrame { background-color: #0f172a; border: 1px solid #334155; border-radius: 6px; }")
        self._image = None          # QImage 原图（等比铺入画布）
        self._roi = None            # 源坐标 dict（x,y,w,h，像素）
        self._img_w = 640           # 虚拟画布尺寸（有图=图尺寸，无图=640x480）
        self._img_h = 480
        self._scale = 1.0           # 源坐标 -> 画布像素 缩放
        self._offset_x = 0.0
        self._offset_y = 0.0
        self._drag_mode = None      # "move" / "resize" / None
        self._drag_start = None
        self._drag_orig = None
        self._resize_handle = 16    # 右下角手柄尺寸（画布像素）
        self.setMouseTracking(True)
        self.changed = None         # 回调(roi_dict)

    # ---------- 数据 ----------
    def set_image(self, qimage: QImage):
        """设置图像背景；有图时虚拟画布=图像像素尺寸"""
        self._image = qimage
        if qimage and not qimage.isNull():
            self._img_w = qimage.width()
            self._img_h = qimage.height()
        else:
            self._img_w, self._img_h = 640, 480
        self._recalc()
        self.update()

    def set_roi(self, roi: dict):
        self._roi = dict(roi) if roi else {"x": 120, "y": 120, "w": 420, "h": 420}
        self.update()

    def get_roi(self) -> dict:
        return dict(self._roi) if self._roi else {}

    def _rect_px(self) -> QRect:
        """ROI 在画布上的像素矩形"""
        r = self._roi
        x = self._offset_x + r["x"] * self._scale
        y = self._offset_y + r["y"] * self._scale
        w = r["w"] * self._scale
        h = r["h"] * self._scale
        return QRect(int(x), int(y), int(w), int(h))

    def _clamp_roi(self, roi: dict):
        """按虚拟画布尺寸 clamp"""
        roi["x"] = int(max(0, min(self._img_w - 1, roi["x"])))
        roi["y"] = int(max(0, min(self._img_h - 1, roi["y"])))
        roi["w"] = int(max(1, min(self._img_w - roi["x"], roi["w"])))
        roi["h"] = int(max(1, min(self._img_h - roi["y"], roi["h"])))
        return roi

    # ---------- 布局 ----------
    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._recalc()

    def _recalc(self):
        cw, ch = self.width(), self.height()
        if cw <= 0 or ch <= 0:
            return
        self._scale = min((cw - 2 * _MARGIN) / self._img_w,
                          (ch - 2 * _MARGIN) / self._img_h)
        self._scale = max(0.05, min(4.0, self._scale))
        self._offset_x = (cw - self._img_w * self._scale) / 2
        self._offset_y = (ch - self._img_h * self._scale) / 2

    # ---------- 鼠标交互 ----------
    def _px_to_img(self, pos) -> tuple:
        x = (pos.x() - self._offset_x) / self._scale
        y = (pos.y() - self._offset_y) / self._scale
        return x, y

    def _hit_test(self, pos) -> str:
        r = self._rect_px()
        if not r.isValid():
            return None
        # 右下角手柄优先
        if abs(pos.x() - r.right()) <= self._resize_handle and \
                abs(pos.y() - r.bottom()) <= self._resize_handle:
            return "resize"
        if r.adjusted(-4, -4, 4, 4).contains(pos):
            return "move"
        return None

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton and self._roi:
            self._drag_mode = self._hit_test(event.pos())
            if self._drag_mode:
                self._drag_start = event.pos()
                self._drag_orig = dict(self._roi)
                self.setCursor(Qt.ClosedHandCursor if self._drag_mode == "move"
                               else Qt.SizeFDiagCursor)
                event.accept()
                return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._drag_mode and self._drag_start is not None:
            dx = (event.pos().x() - self._drag_start.x()) / self._scale
            dy = (event.pos().y() - self._drag_start.y()) / self._scale
            roi = dict(self._drag_orig)
            if self._drag_mode == "move":
                roi["x"] = self._drag_orig["x"] + int(dx)
                roi["y"] = self._drag_orig["y"] + int(dy)
            else:  # resize
                roi["w"] = self._drag_orig["w"] + int(dx)
                roi["h"] = self._drag_orig["h"] + int(dy)
            self._clamp_roi(roi)
            self._roi = roi
            if self.changed:
                self.changed(dict(roi))
            self.update()
            event.accept()
            return
        # hover 光标提示
        if self._roi:
            mode = self._hit_test(event.pos())
            self.setCursor(Qt.SizeFDiagCursor if mode == "resize"
                           else (Qt.OpenHandCursor if mode == "move" else Qt.ArrowCursor))
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if self._drag_mode:
            self._drag_mode = None
            self._drag_start = None
            self.unsetCursor()
            event.accept()
            return
        super().mouseReleaseEvent(event)

    # ---------- 绘制 ----------
    def paintEvent(self, event):
        super().paintEvent(event)
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        # 虚拟画布底框（灰底）
        ref_w = self._img_w * self._scale
        ref_h = self._img_h * self._scale
        p.setPen(QPen(QColor("#334155"), 1))
        p.setBrush(QColor("#111c2e"))
        p.drawRect(int(self._offset_x), int(self._offset_y), int(ref_w), int(ref_h))
        p.setPen(QPen(QColor("#475569"), 1, Qt.DashLine))
        for i in range(1, 4):
            x = self._offset_x + ref_w * i / 4
            p.drawLine(int(x), int(self._offset_y), int(x), int(self._offset_y + ref_h))
        for i in range(1, 3):
            y = self._offset_y + ref_h * i / 3
            p.drawLine(int(self._offset_x), int(y),
                       int(self._offset_x + ref_w), int(y))

        # 图像背景（等比铺入画布）
        if self._image and not self._image.isNull():
            img_w, img_h = self._image.width(), self._image.height()
            if img_w > 0 and img_h > 0:
                scaled = QPixmap.fromImage(self._image.scaled(
                    int(img_w * self._scale), int(img_h * self._scale),
                    Qt.KeepAspectRatio, Qt.SmoothTransformation))
                p.drawPixmap(int(self._offset_x), int(self._offset_y), scaled)

        # ROI 矩形
        if self._roi:
            r = self._rect_px()
            pen = QPen(QColor("#22c55e"), 2)
            pen.setStyle(Qt.DashLine)
            p.setPen(pen)
            p.setBrush(QColor(34, 197, 94, 30))
            p.drawRect(r)
            # 尺寸标签
            p.setPen(QPen(QColor("#22c55e"), 1))
            p.setFont(QFont("Microsoft YaHei", 10))
            label = f"{self._roi['w']}x{self._roi['h']}"
            p.drawText(r.left() + 4, max(14, r.top() - 8), label)
            # 右下角缩放手柄
            p.fillRect(r.right() - self._resize_handle, r.bottom() - self._resize_handle,
                       self._resize_handle, self._resize_handle,
                       QColor(34, 197, 94, 200))
            p.setPen(QPen(QColor("#052e16"), 1))
            p.drawLine(r.right() - 5, r.bottom() - 3, r.right() - 3, r.bottom() - 5)
        p.end()


class RoiEditDialog(QDialog):
    """单 ROI 编辑对话框（表单 + 可视化画布双向同步）

    兼容旧接口：values() 返回 x/y/w/h dict；result_roi() 返回完整 roi dict。
    """

    def __init__(self, roi: dict, parent=None, image: QImage = None):
        super().__init__(parent)
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.Dialog)
        self.setWindowTitle(f"编辑 {roi.get('name', 'ROI')}")
        self.setModal(True)
        self.setMinimumWidth(600)
        self.setStyleSheet(
            "QDialog { background-color: #0f172a; }"
            "QLabel { color: #cbd5e1; font-size: 15px; background: transparent; }"
            "QLineEdit, QSpinBox { background-color: #1e293b; color: #e2e8f0;"
            " border: 1px solid #334155; border-radius: 4px; padding: 2px 6px;"
            " font-size: 15px; }"
            "QPushButton { background-color: #1e293b; color: #cbd5e1;"
            " border: 1px solid #334155; border-radius: 4px; font-size: 15px;"
            " padding: 6px 18px; }"
            "QPushButton:hover { background-color: #334155; }"
            "QPushButton#btnPrimary { background-color: #2563eb; color: #fff;"
            " border-color: #2563eb; }"
            "QPushButton#btnPrimary:hover { background-color: #3b76f0; }"
            "QCheckBox { color: #cbd5e1; font-size: 15px; background: transparent; }"
        )
        self._roi = dict(roi)
        self._build_ui(image)

    def _build_ui(self, image):
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 14, 16, 14)
        root.setSpacing(10)

        # 表单
        form = QGridLayout()
        form.setHorizontalSpacing(10)
        form.setVerticalSpacing(8)

        form.addWidget(QLabel("名称"), 0, 0)
        self.edit_name = QLineEdit(self._roi.get("name", "ROI"))
        self.edit_name.setFixedHeight(34)
        form.addWidget(self.edit_name, 0, 1)

        self.check_enabled = QCheckBox("启用此 ROI")
        self.check_enabled.setChecked(bool(self._roi.get("enabled", True)))
        form.addWidget(self.check_enabled, 0, 2, 1, 2)

        form.addWidget(QLabel("左上角 X"), 1, 0)
        self.spin_x = _spin(0, 99999, int(self._roi.get("x", 0)))
        form.addWidget(self.spin_x, 1, 1)
        form.addWidget(QLabel("左上角 Y"), 1, 2)
        self.spin_y = _spin(0, 99999, int(self._roi.get("y", 0)))
        form.addWidget(self.spin_y, 1, 3)

        form.addWidget(QLabel("宽度 W"), 2, 0)
        self.spin_w = _spin(1, 99999, int(self._roi.get("w", 100)))
        form.addWidget(self.spin_w, 2, 1)
        form.addWidget(QLabel("高度 H"), 2, 2)
        self.spin_h = _spin(1, 99999, int(self._roi.get("h", 100)))
        form.addWidget(self.spin_h, 2, 3)
        root.addLayout(form)

        # 画布
        hint = QLabel("提示：可直接在画布上拖动绿色框移动，拖右下角手柄缩放")
        hint.setStyleSheet("color: #64748b; font-size: 14px;")
        root.addWidget(hint)
        self.canvas = RoiCanvas()
        if image is not None:
            self.canvas.set_image(image)
        self.canvas.set_roi(self._roi)
        self.canvas.changed = self._on_canvas_changed
        root.addWidget(self.canvas)

        # 按钮
        btns = QHBoxLayout()
        btns.addStretch()
        btn_cancel = QPushButton("取消")
        btn_cancel.clicked.connect(self.reject)
        btn_ok = QPushButton("确定")
        btn_ok.setObjectName("btnPrimary")
        btn_ok.clicked.connect(self._on_ok)
        btns.addWidget(btn_cancel)
        btns.addWidget(btn_ok)
        root.addLayout(btns)

        # 表单 <-> 画布 双向同步
        for s in (self.spin_x, self.spin_y, self.spin_w, self.spin_h):
            s.valueChanged.connect(self._on_spin_changed)

    # ---------- 同步 ----------
    def _sync_from_canvas(self):
        r = self.canvas.get_roi()
        for s, key in ((self.spin_x, "x"), (self.spin_y, "y"),
                       (self.spin_w, "w"), (self.spin_h, "h")):
            s.blockSignals(True)
            s.setValue(int(r.get(key, 0)))
            s.blockSignals(False)

    def _on_canvas_changed(self, roi):
        self._roi = roi
        self._sync_from_canvas()

    def _on_spin_changed(self, *_):
        self._roi["x"] = self.spin_x.value()
        self._roi["y"] = self.spin_y.value()
        self._roi["w"] = self.spin_w.value()
        self._roi["h"] = self.spin_h.value()
        self.canvas.set_roi(self._roi)

    def _on_ok(self):
        self._roi["name"] = self.edit_name.text().strip() or "ROI"
        self._roi["enabled"] = self.check_enabled.isChecked()
        self.accept()

    def result_roi(self) -> dict:
        return dict(self._roi)

    def values(self) -> dict:
        """兼容旧接口：仅返回坐标字段"""
        r = self._roi
        return {k: int(r.get(k, 0)) for k in ("x", "y", "w", "h")}

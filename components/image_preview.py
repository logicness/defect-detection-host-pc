"""
图像预览组件：缩放/平移/ROI 绿框/缺陷红框/NG 浮窗
滚轮缩放(30%~500%)、中键拖动平移、双击还原
"""
from PyQt5.QtWidgets import QWidget
from PyQt5.QtCore import Qt, QPointF, QRectF
from PyQt5.QtGui import QPainter, QColor, QPen, QFont, QImage


class ImagePreview(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(320, 240)
        self.setStyleSheet("background-color:#0f172a; border:1px solid #2a3441;")
        self.setMouseTracking(True)
        self._img = None            # QImage
        self._rois = []             # [{x,y,w,h,enabled}]
        self._dets = []             # [(cls, conf, x1,y1,x2,y2)]
        self._ng_conf = None        # NG 浮窗置信度
        self._zoom = 1.0
        self._pan = QPointF(0, 0)
        self._dragging = False
        self._drag_last = QPointF()

    # ---------------- 数据接口 ----------------
    def set_image(self, img: QImage):
        self._img = img
        self.update()

    def get_image(self) -> QImage:
        """返回当前显示的图像（供 ROI 编辑对话框等取底图）"""
        return self._img

    def set_rois(self, rois: list):
        self._rois = [r for r in rois if r.get("enabled", True)]
        self.update()

    def set_detections(self, dets: list):
        self._dets = dets or []
        self._ng_conf = dets[0][1] if dets else None
        self.update()

    def clear_detections(self):
        self._dets, self._ng_conf = [], None
        self.update()

    # ---------------- 坐标映射 ----------------
    def _view(self):
        """返回 (scale, ox, oy)：图像坐标 → 控件坐标"""
        if self._img is None or self._img.isNull():
            return 1.0, 0, 0
        iw, ih = self._img.width(), self._img.height()
        base = min(self.width() / iw, self.height() / ih)
        s = base * self._zoom
        ox = (self.width() - iw * s) / 2 + self._pan.x()
        oy = (self.height() - ih * s) / 2 + self._pan.y()
        return s, ox, oy

    # ---------------- 绘制 ----------------
    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.fillRect(self.rect(), QColor("#0f172a"))
        if self._img is None or self._img.isNull():
            p.setPen(QColor("#64748b"))
            p.setFont(QFont("Microsoft YaHei", 14))
            p.drawText(self.rect(), Qt.AlignCenter, "等待视频流...")
            return
        s, ox, oy = self._view()
        # 图像
        target = QRectF(ox, oy, self._img.width() * s, self._img.height() * s)
        p.drawImage(target, self._img)

        # 框线裁剪到图像区域（ROI/缺陷坐标可能超出图像边界）
        p.save()
        p.setClipRect(target)

        # ROI 绿框
        pen = QPen(QColor("#22c55e"), 2)
        p.setPen(pen)
        for r in self._rois:
            p.drawRect(QRectF(ox + r["x"] * s, oy + r["y"] * s, r["w"] * s, r["h"] * s))

        # 缺陷红框
        p.setPen(QPen(QColor("#ef4444"), 2))
        for d in self._dets:
            _, _, x1, y1, x2, y2 = d[:6]
            p.drawRect(QRectF(ox + x1 * s, oy + y1 * s, (x2 - x1) * s, (y2 - y1) * s))
        p.restore()

        # NG 浮窗（右上）
        if self._ng_conf is not None:
            bx, by = self.width() - 132, 14
            p.setPen(QPen(QColor("#ef4444"), 2))
            p.setBrush(QColor(30, 10, 12, 220))
            p.drawRoundedRect(bx, by, 118, 84, 6, 6)
            p.setPen(QColor("#ef4444"))
            p.setFont(QFont("Microsoft YaHei", 28, QFont.Bold))
            p.drawText(QRectF(bx, by + 4, 118, 48), Qt.AlignCenter, "NG")
            p.setFont(QFont("Microsoft YaHei", 12))
            p.setPen(QColor("#fca5a5"))
            p.drawText(QRectF(bx, by + 54, 118, 26), Qt.AlignCenter,
                       f"置信度: {int(self._ng_conf * 100)}%")

    # ---------------- 交互 ----------------
    def zoom_by(self, delta: float):
        self._zoom = min(5.0, max(0.3, self._zoom + delta))
        self.update()

    def get_zoom(self) -> float:
        return self._zoom

    def wheelEvent(self, event):
        self.zoom_by(0.1 if event.angleDelta().y() > 0 else -0.1)

    def mousePressEvent(self, event):
        if event.button() == Qt.MiddleButton:
            self._dragging = True
            self._drag_last = QPointF(event.localPos())

    def mouseMoveEvent(self, event):
        if self._dragging:
            pos = QPointF(event.localPos())
            self._pan += pos - self._drag_last
            self._drag_last = pos
            self.update()

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MiddleButton:
            self._dragging = False

    def mouseDoubleClickEvent(self, event):
        self._zoom, self._pan = 1.0, QPointF(0, 0)
        self.update()

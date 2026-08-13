"""
图像预览组件：缩放/平移/ROI 绿框/缺陷红框/NG 浮窗
滚轮缩放(30%~500%)、中键拖动平移、双击还原
新增：左键可直接在画面上拖动 ROI 或拖右下角手柄缩放
"""
from PyQt5.QtWidgets import QWidget
from PyQt5.QtCore import Qt, QPointF, QRectF, pyqtSignal
from PyQt5.QtGui import QPainter, QColor, QPen, QFont, QImage


class ImagePreview(QWidget):
    roi_edited = pyqtSignal(int, dict)  # (roi_index_in_full_list, roi_dict)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(320, 240)
        self.setStyleSheet("background-color:#0f172a; border:1px solid #2a3441;")
        self.setMouseTracking(True)
        self._img = None            # QImage
        self._rois = []             # 完整 ROI 列表 [{x,y,w,h,enabled}, ...]
        self._dets = []             # [(cls, conf, x1,y1,x2,y2)]
        self._ng_conf = None        # NG 浮窗置信度
        self._zoom = 1.0
        self._pan = QPointF(0, 0)
        self._pan_dragging = False  # 中键平移状态
        self._pan_last = QPointF()

        # ROI 鼠标编辑状态
        self._roi_drag_idx = -1     # 正在拖拽的 ROI 在 _rois 中的索引
        self._roi_drag_mode = None  # "move" / "resize"
        self._roi_drag_start = None  # 控件坐标 QPointF
        self._roi_drag_orig = None   # 拖拽开始时的 ROI 副本
        self._roi_hover_idx = -1     # 当前悬停的 ROI 索引
        self._roi_hover_mode = None
        self._handle_size = 12       # 右下角手柄尺寸（控件像素）

    # ---------------- 数据接口 ----------------
    def set_image(self, img: QImage):
        self._img = img
        self.update()

    def get_image(self) -> QImage:
        """返回当前显示的图像（供 ROI 编辑对话框等取底图）"""
        return self._img

    def set_rois(self, rois: list):
        self._rois = list(rois)
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

    def _pos_to_img(self, pos):
        """控件坐标 → 图像坐标"""
        s, ox, oy = self._view()
        if s <= 0:
            return 0, 0
        return (pos.x() - ox) / s, (pos.y() - oy) / s

    def _roi_rect(self, roi):
        """返回 ROI 在控件上的 QRectF"""
        s, ox, oy = self._view()
        return QRectF(ox + roi["x"] * s, oy + roi["y"] * s,
                      roi["w"] * s, roi["h"] * s)

    # ---------------- ROI 命中检测 ----------------
    def _enabled_rois(self):
        for i, r in enumerate(self._rois):
            if r.get("enabled", True):
                yield i, r

    def _hit_test(self, pos):
        """返回 (roi_index, mode) 或 None；优先 resize 手柄，再 move 框体"""
        # 逆序遍历，让上层（后绘制）ROI 优先响应
        for idx, roi in reversed(list(self._enabled_rois())):
            r = self._roi_rect(roi)
            if not r.isValid():
                continue
            # resize 手柄区域
            handle = QRectF(r.right() - self._handle_size,
                            r.bottom() - self._handle_size,
                            self._handle_size, self._handle_size)
            if handle.contains(pos):
                return idx, "resize"
            # move 框体区域（含轻微扩展便于命中）
            if r.adjusted(-3, -3, 3, 3).contains(pos):
                return idx, "move"
        return None

    def _clamp_roi(self, roi: dict):
        """限制 ROI 在图像范围内"""
        if self._img is None or self._img.isNull():
            iw, ih = 640, 480
        else:
            iw, ih = self._img.width(), self._img.height()
        roi["x"] = int(max(0, min(iw - 1, roi["x"])))
        roi["y"] = int(max(0, min(ih - 1, roi["y"])))
        roi["w"] = int(max(10, min(iw - roi["x"], roi["w"])))
        roi["h"] = int(max(10, min(ih - roi["y"], roi["h"])))
        return roi

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
        for idx, r in self._enabled_rois():
            rr = self._roi_rect(r)
            active = (idx == self._roi_drag_idx or idx == self._roi_hover_idx)
            pen = QPen(QColor("#4ade80" if active else "#22c55e"),
                       3 if active else 2)
            p.setPen(pen)
            p.drawRect(rr)
            # 右下角手柄
            if active:
                p.fillRect(rr.right() - self._handle_size,
                           rr.bottom() - self._handle_size,
                           self._handle_size, self._handle_size,
                           QColor("#4ade80"))

        # 缺陷框：双层描边 + 角标 + 标签，确保在复杂背景下清晰可辨
        for d in self._dets:
            cls, conf, x1, y1, x2, y2 = d[:6]
            rx, ry = ox + x1 * s, oy + y1 * s
            rw, rh = (x2 - x1) * s, (y2 - y1) * s
            rect = QRectF(rx, ry, rw, rh)

            # 外层高对比描边（黑色半透明）
            p.setPen(QPen(QColor(0, 0, 0, 180), 4))
            p.drawRect(rect)
            # 内层高亮描边（亮红）
            p.setPen(QPen(QColor("#ff5252"), 2))
            p.drawRect(rect)

            # 四角小标记，提高定位精度
            corner = min(12.0, min(rw, rh) * 0.25)
            if corner > 3:
                p.setPen(QPen(QColor("#ffffff"), 2))
                # 左上
                p.drawLine(rx, ry + corner, rx, ry)
                p.drawLine(rx, ry, rx + corner, ry)
                # 右上
                p.drawLine(rx + rw - corner, ry, rx + rw, ry)
                p.drawLine(rx + rw, ry, rx + rw, ry + corner)
                # 左下
                p.drawLine(rx, ry + rh - corner, rx, ry + rh)
                p.drawLine(rx, ry + rh, rx + corner, ry + rh)
                # 右下
                p.drawLine(rx + rw - corner, ry + rh, rx + rw, ry + rh)
                p.drawLine(rx + rw, ry + rh - corner, rx + rw, ry + rh)

            # 左上角标签背景
            label = f"{cls} {conf:.2f}"
            p.setFont(QFont("Microsoft YaHei", 10, QFont.Bold))
            fm = p.fontMetrics()
            tw = fm.horizontalAdvance(label) + 8
            th = fm.height() + 4
            lx, ly = rx, ry - th
            if ly < target.top():
                ly = ry  # 标签贴顶时放到框内
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(239, 68, 68, 220))
            p.drawRect(lx, ly, tw, th)
            p.setPen(QColor("#ffffff"))
            p.drawText(lx + 4, ly + fm.ascent() + 2, label)

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
        pos = event.localPos()
        if event.button() == Qt.LeftButton:
            hit = self._hit_test(pos)
            if hit is not None:
                self._roi_drag_idx, self._roi_drag_mode = hit
                self._roi_drag_start = QPointF(pos)
                self._roi_drag_orig = dict(self._rois[self._roi_drag_idx])
                self._roi_hover_idx = self._roi_drag_idx
                self._roi_hover_mode = self._roi_drag_mode
                cursor = (Qt.SizeFDiagCursor if self._roi_drag_mode == "resize"
                          else Qt.ClosedHandCursor)
                self.setCursor(cursor)
                event.accept()
                return
        elif event.button() == Qt.MiddleButton:
            self._pan_dragging = True
            self._pan_last = QPointF(pos)

    def mouseMoveEvent(self, event):
        pos = event.localPos()
        # ROI 拖拽中
        if self._roi_drag_idx >= 0 and self._roi_drag_mode and self._roi_drag_start is not None:
            x0, y0 = self._pos_to_img(self._roi_drag_start)
            x1, y1 = self._pos_to_img(pos)
            dx = x1 - x0
            dy = y1 - y0
            roi = dict(self._roi_drag_orig)
            if self._roi_drag_mode == "move":
                roi["x"] = int(self._roi_drag_orig["x"] + dx)
                roi["y"] = int(self._roi_drag_orig["y"] + dy)
            else:  # resize
                roi["w"] = int(self._roi_drag_orig["w"] + dx)
                roi["h"] = int(self._roi_drag_orig["h"] + dy)
            self._clamp_roi(roi)
            self._rois[self._roi_drag_idx] = roi
            self.update()
            event.accept()
            return
        # 中键平移
        if self._pan_dragging:
            self._pan += QPointF(pos) - self._pan_last
            self._pan_last = QPointF(pos)
            self.update()
            return
        # 悬停光标提示
        hit = self._hit_test(pos)
        if hit is not None:
            self._roi_hover_idx, self._roi_hover_mode = hit
            cursor = (Qt.SizeFDiagCursor if hit[1] == "resize"
                      else Qt.OpenHandCursor)
            self.setCursor(cursor)
        else:
            self._roi_hover_idx = -1
            self._roi_hover_mode = None
            self.unsetCursor()
        self.update()

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton and self._roi_drag_idx >= 0:
            roi = self._rois[self._roi_drag_idx]
            self.roi_edited.emit(self._roi_drag_idx, dict(roi))
            self._roi_drag_idx = -1
            self._roi_drag_mode = None
            self._roi_drag_start = None
            self._roi_drag_orig = None
            self.unsetCursor()
            event.accept()
            return
        if event.button() == Qt.MiddleButton:
            self._pan_dragging = False

    def mouseDoubleClickEvent(self, event):
        self._zoom, self._pan = 1.0, QPointF(0, 0)
        self.update()

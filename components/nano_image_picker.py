# -*- coding: utf-8 -*-
"""
下位机图片选择对话框（2026-08-24 重构，对齐「本地图片」交互）
=================================================
- 点「▣ 下位机图片」→ 弹出本对话框，列出 Nano 固定文件夹的图片缩略图
- 单选/多选由主界面「单次检测/多次检测」模式决定（multi 参数）
- 用户确定后返回 selected_names + image_dir；检测由主界面统一流程处理
  （选图后主界面预览 → 点「开始检测」→ nano_detect_request 单帧 / 批量续接）
"""
import base64

from PyQt5.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QMessageBox,
    QListWidget, QListWidgetItem, QAbstractItemView, QInputDialog,
    QApplication
)
from PyQt5.QtCore import Qt, QSize
from PyQt5.QtGui import QPixmap, QIcon

from components.common_widgets import Card

_THUMB = QSize(120, 90)


class NanoImagePickerDialog(QDialog):
    """下位机图片选择对话框：Nano 文件夹缩略图网格，单选/多选"""

    def __init__(self, tcp_client=None, multi=True, default_dir="", parent=None):
        super().__init__(parent)
        self._tcp = tcp_client
        self._multi = multi
        self._wired = False
        self.image_dir = default_dir or ""
        self.selected_names = []   # 确定时选中的文件名列表
        self._images = []

        self.setWindowTitle("选择下位机图片")
        self.setMinimumSize(720, 540)
        self.resize(900, 640)
        self._build_ui()
        self._wire()
        self._refresh()

    # ---------------- UI ----------------
    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(10)

        top = QHBoxLayout()
        self.lbl_dir = QLabel("下位机图片目录: --")
        self.lbl_dir.setStyleSheet("color:#e2e8f0; font-size:14px; background:transparent;")
        self.lbl_dir.setWordWrap(True)
        top.addWidget(self.lbl_dir, 1)
        self.btn_refresh = QPushButton("↺ 刷新")
        self.btn_refresh.setFixedHeight(32)
        self.btn_refresh.clicked.connect(self._refresh)
        self.btn_set_dir = QPushButton("设置文件夹...")
        self.btn_set_dir.setFixedHeight(32)
        self.btn_set_dir.setToolTip("切换下位机图片文件夹（需在 Nano 上真实存在）")
        self.btn_set_dir.clicked.connect(self._on_set_dir)
        top.addWidget(self.btn_refresh)
        top.addWidget(self.btn_set_dir)
        root.addLayout(top)

        card = Card("下位机图片（双击或点确定选择）" if self._multi
                    else "下位机图片（双击选择）")
        self.list = QListWidget()
        self.list.setViewMode(QListWidget.IconMode)
        self.list.setIconSize(_THUMB)
        self.list.setResizeMode(QListWidget.Adjust)
        self.list.setMovement(QListWidget.Static)
        self.list.setSpacing(6)
        self.list.setSelectionMode(
            QAbstractItemView.ExtendedSelection if self._multi
            else QAbstractItemView.SingleSelection)
        self.list.itemDoubleClicked.connect(lambda _it: self._accept())
        card.body.addWidget(self.list, 1)
        root.addWidget(card, 1)

        self.lbl_hint = QLabel("未连接下位机时无法列出图片")
        self.lbl_hint.setStyleSheet(
            "color:#94a3b8; font-size:13px; background:transparent;")
        root.addWidget(self.lbl_hint)

        btns = QHBoxLayout()
        btns.addStretch()
        self.btn_ok = QPushButton("确定")
        self.btn_ok.setObjectName("btnPrimary")
        self.btn_ok.setFixedWidth(120)
        self.btn_ok.clicked.connect(self._accept)
        btn_cancel = QPushButton("取消")
        btn_cancel.setFixedWidth(120)
        btn_cancel.clicked.connect(self.reject)
        btns.addWidget(self.btn_ok)
        btns.addWidget(btn_cancel)
        root.addLayout(btns)

        if self._tcp is None or not getattr(self._tcp, "is_connected", False):
            self.lbl_dir.setText("下位机图片目录: --（Nano 未连接）")
            self.lbl_hint.setText("提示: 请先在上位机连接 Nano（实时页「重新连接」）")
            self.btn_ok.setEnabled(False)
            self.btn_refresh.setEnabled(False)
            self.btn_set_dir.setEnabled(False)

    # ---------------- TCP 接线 ----------------
    def _wire(self):
        if self._wired or self._tcp is None:
            return
        t = self._tcp
        t.nano_image_dir_received.connect(self._on_dir)
        t.nano_images_received.connect(self._on_images)
        t.disconnected.connect(self._on_disconnected)
        self._wired = True

    def _unwire(self):
        if not self._wired or self._tcp is None:
            return
        t = self._tcp
        for sig in (t.nano_image_dir_received, t.nano_images_received, t.disconnected):
            try:
                sig.disconnect(self._on_dir)
                sig.disconnect(self._on_images)
                sig.disconnect(self._on_disconnected)
            except Exception:
                pass
        self._wired = False

    # ---------------- 行为 ----------------
    def _refresh(self):
        if self._tcp is None or not getattr(self._tcp, "is_connected", False):
            return
        self.lbl_dir.setText("下位机图片目录: 查询中...")
        self._tcp.request_nano_image_dir("get")
        self._tcp.request_nano_images(offset=0, limit=500, thumb=True, thumb_size=256)

    def _on_dir(self, msg):
        if msg.get("ok"):
            self.image_dir = msg.get("dir", "")
            self.lbl_dir.setText(f"下位机图片目录: {self.image_dir}")

    def _on_images(self, msg):
        if not msg.get("ok"):
            self.lbl_hint.setText(f"图片列表获取失败: {msg.get('error', '')}")
            return
        self._images = [i for i in msg.get("images", [])]
        self.list.clear()
        for item in self._images:
            it = QListWidgetItem()
            thumb = item.get("thumb_b64", "")
            if thumb:
                pm = QPixmap()
                pm.loadFromData(base64.b64decode(thumb))
                it.setIcon(QIcon(pm))
            it.setText(item.get("name", ""))
            it.setData(Qt.UserRole, item.get("name", ""))
            it.setSizeHint(QSize(150, 116))
            it.setToolTip(item.get("name", ""))
            self.list.addItem(it)
        total = msg.get("total", len(self._images))
        self.lbl_dir.setText(f"下位机图片目录: {self.image_dir}（共 {total} 张）")
        self.lbl_hint.setText(
            ("提示: 按住 Ctrl/Shift 可多选，双击直接确定"
             if self._multi else "提示: 双击图片直接选择") if self._images
            else "提示: 图片目录为空，请先在下位机 images/input 放入图片")
        self.btn_ok.setEnabled(bool(self._images))

    def _on_disconnected(self):
        self.lbl_dir.setText("下位机图片目录: --（Nano 已断开）")
        self.btn_ok.setEnabled(False)
        self.btn_refresh.setEnabled(False)
        self.btn_set_dir.setEnabled(False)

    def _on_set_dir(self):
        cur = self.image_dir or "/home/nvidia/defect_detection/images/input"
        path, ok = QInputDialog.getText(
            self, "设置下位机图片文件夹",
            "输入 Nano 上的图片目录路径（需真实存在）:", text=cur)
        if ok and path.strip():
            self._tcp.request_nano_image_dir("set", path.strip())

    def _accept(self):
        items = self.list.selectedItems()
        names = [i.data(Qt.UserRole) for i in items]
        if not names:
            if QApplication.platformName() != "offscreen":
                QMessageBox.information(self, "选择下位机图片", "请先选择至少一张图片。")
            return
        if not self._multi and len(names) > 1:
            names = names[:1]
        self.selected_names = names
        self.accept()

    def reject(self):
        self._unwire()
        super().reject()

    def accept(self):
        self._unwire()
        super().accept()

    def closeEvent(self, event):
        self._unwire()
        super().closeEvent(event)

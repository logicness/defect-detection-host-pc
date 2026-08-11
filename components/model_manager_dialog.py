# -*- coding: utf-8 -*-
"""
模型管理对话框（RK3568 版）
==========================
- Nano 模型清单：刷新 / 切换加载（走 TCP model_list / model_load 协议）
- PC 本地模型：浏览 .onnx/.pt 文件，设为本地推理模型（无 Nano 也可检测）
- 依赖：core.tcp_client.TCPClient（model_list_received / model_load_received）
"""
import os

from PyQt5.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QLineEdit,
    QTableWidget, QTableWidgetItem, QHeaderView, QAbstractItemView,
    QFileDialog, QMessageBox
)
from PyQt5.QtCore import Qt, pyqtSignal

_LOCAL_EXTS = (".onnx", ".pt")


class ModelManagerDialog(QDialog):
    """模型管理对话框：Nano 端清单 + PC 本地模型"""

    local_model_selected = pyqtSignal(str)   # 本地模型路径已选择

    def __init__(self, tcp, parent=None):
        super().__init__(parent)
        self.tcp = tcp
        self.setWindowTitle("模型管理")
        self.setMinimumSize(760, 520)
        self.resize(860, 580)
        self._models = []
        self._active = ""
        self._build_ui()
        self._connect_signals()
        self.refresh_list()

    # ---------- UI ----------
    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 14, 16, 14)
        root.setSpacing(10)

        # 当前推理源状态
        self.lbl_status = QLabel("推理源：未连接下位机（可选用本地模型）")
        self.lbl_status.setStyleSheet(
            "color:#94a3b8; font-size:15px; background:transparent;")
        root.addWidget(self.lbl_status)

        # Nano 模型清单
        sec_nano = QLabel("下位机（Nano）模型")
        sec_nano.setStyleSheet(
            "border-left:3px solid #2563eb; padding-left:8px;"
            "font-size:16px; font-weight:600; color:#f1f5f9; background:transparent;")
        root.addWidget(sec_nano)

        self.table = QTableWidget()
        self.table.setColumnCount(3)
        self.table.setHorizontalHeaderLabels(["模型名称", "状态", "说明"])
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setShowGrid(False)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        self.table.itemSelectionChanged.connect(self._on_select)
        root.addWidget(self.table, 1)

        row = QHBoxLayout()
        self.btn_refresh = QPushButton("刷新清单")
        self.btn_refresh.clicked.connect(self.refresh_list)
        self.btn_load = QPushButton("加载所选模型")
        self.btn_load.setObjectName("btnPrimary")
        self.btn_load.setEnabled(False)
        self.btn_load.clicked.connect(self._on_load_selected)
        row.addWidget(self.btn_refresh)
        row.addWidget(self.btn_load)
        row.addStretch()
        self.lbl_nano = QLabel("")
        self.lbl_nano.setStyleSheet("color:#64748b; background:transparent;")
        row.addWidget(self.lbl_nano)
        root.addLayout(row)

        # PC 本地模型
        sec_local = QLabel("PC 本地模型（无需 Nano 在线，开始检测时启用本地推理）")
        sec_local.setStyleSheet(
            "border-left:3px solid #22c55e; padding-left:8px;"
            "font-size:16px; font-weight:600; color:#f1f5f9; background:transparent;")
        root.addWidget(sec_local)

        brow = QHBoxLayout()
        self.edit_local = QLineEdit()
        self.edit_local.setPlaceholderText("选择本地 .onnx / .pt 模型文件")
        btn_browse = QPushButton("浏览...")
        btn_browse.clicked.connect(self._on_browse)
        btn_use = QPushButton("设为本地推理模型")
        btn_use.setObjectName("btnPrimary")
        btn_use.clicked.connect(self._on_use_local)
        brow.addWidget(self.edit_local, 1)
        brow.addWidget(btn_browse)
        brow.addWidget(btn_use)
        root.addLayout(brow)

        hint = QLabel("提示：本地模型在「开始检测」时自动启用；连接下位机时优先使用下位机推理")
        hint.setStyleSheet("color:#64748b; font-size:14px; background:transparent;")
        root.addWidget(hint)

        btns = QHBoxLayout()
        btns.addStretch()
        btn_close = QPushButton("关闭")
        btn_close.setFixedWidth(120)
        btn_close.clicked.connect(self.accept)
        btns.addWidget(btn_close)
        root.addLayout(btns)

    def _connect_signals(self):
        self.tcp.model_list_received.connect(self._on_list_received)
        self.tcp.model_load_received.connect(self._on_load_received)

    # ---------- Nano 清单 ----------
    def refresh_list(self):
        if not self.tcp.is_connected:
            self.lbl_status.setText("推理源：下位机未连接（可选用本地模型）")
            self.lbl_nano.setText("未连接下位机")
            self.table.setRowCount(0)
            return
        self.lbl_status.setText("推理源：下位机已连接")
        self.lbl_nano.setText("获取清单中...")
        self.tcp.request_model_list()

    def _on_list_received(self, payload: dict):
        self._models = payload.get("models", [])
        self._active = payload.get("active", "")
        self.table.setRowCount(0)
        for m in self._models:
            name = m.get("name", "") if isinstance(m, dict) else str(m)
            active = name == self._active
            row = self.table.rowCount()
            self.table.insertRow(row)
            self.table.setItem(row, 0, QTableWidgetItem(name))
            self.table.setItem(
                row, 1,
                QTableWidgetItem("● 激活中" if active else ""))
            if active:
                self.table.item(row, 1).setForeground(
                    Qt.green if False else _qcolor("#22c55e"))
            self.table.setItem(row, 2, QTableWidgetItem(
                m.get("desc", "") if isinstance(m, dict) else ""))
        self.lbl_nano.setText(
            f"共 {len(self._models)} 个模型，激活: {self._active or '无'}")

    def _on_select(self):
        self.btn_load.setEnabled(bool(self.table.selectedItems()))

    def _on_load_selected(self):
        row = self.table.currentRow()
        if row < 0 or row >= len(self._models):
            return
        m = self._models[row]
        name = m.get("name", "") if isinstance(m, dict) else str(m)
        self.tcp.load_model(name)
        self.lbl_nano.setText(f"请求加载: {name}")

    def _on_load_received(self, payload: dict):
        ok = payload.get("ok", False)
        msg = payload.get("message", payload.get("msg", ""))
        name = payload.get("model", "")
        if ok:
            self._active = name
            self.lbl_nano.setText(f"加载成功: {name}")
            self.refresh_list()
        else:
            self.lbl_nano.setText(f"加载失败: {msg or name}")
            QMessageBox.warning(self, "模型加载", f"加载失败: {msg or name}")

    # ---------- 本地模型 ----------
    def _on_browse(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "选择本地模型", "", "模型文件 (*.onnx *.pt)")
        if path:
            self.edit_local.setText(path)

    def _on_use_local(self):
        path = self.edit_local.text().strip()
        if not path:
            QMessageBox.information(self, "模型管理", "请先选择本地模型文件")
            return
        if not os.path.isfile(path):
            QMessageBox.warning(self, "模型管理", f"文件不存在: {path}")
            return
        if not path.lower().endswith(_LOCAL_EXTS):
            QMessageBox.warning(
                self, "模型管理", "仅支持 .onnx / .pt 模型文件")
            return
        self.local_model_selected.emit(path)
        self.lbl_status.setText(f"已选择本地模型：{os.path.basename(path)}")

    # ---------- 关闭时 ----------
    def closeEvent(self, event):
        # 断开信号避免对话框销毁后回调失效
        try:
            self.tcp.model_list_received.disconnect(self._on_list_received)
            self.tcp.model_load_received.disconnect(self._on_load_received)
        except Exception:
            pass
        super().closeEvent(event)


def _qcolor(hex_str: str):
    from PyQt5.QtGui import QColor
    return QColor(hex_str)

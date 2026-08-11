# -*- coding: utf-8 -*-
"""
模型管理对话框（RK3568 版）v2
==========================
左右分栏布局：
- 左侧：下位机（Nano）模型管理（刷新、切换、重连）
- 右侧：PC 本地模型管理（浏览、扫描目录、设为本地推理模型）
- 顶部显示当前推理源状态
依赖：core.tcp_client.TCPClient（model_list_received / model_load_received）
"""
import os
import sys

from PyQt5.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QLineEdit,
    QTableWidget, QTableWidgetItem, QHeaderView, QAbstractItemView,
    QFileDialog, QMessageBox, QSplitter, QListWidget, QListWidgetItem,
    QFrame, QWidget, QApplication
)
from PyQt5.QtCore import Qt, pyqtSignal

from components.model_library import model_quality

# 引入自动扫描脚本（即使 tools 目录未在 sys.path 也加入一次）
_TOOLS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools")
if _TOOLS_DIR not in sys.path:
    sys.path.insert(0, _TOOLS_DIR)
try:
    from auto_scan_models import scan_models as _scan_models, auto_scan as _auto_scan
except Exception:  # 兼容打包/路径异常
    _scan_models = None
    _auto_scan = None

_LOCAL_EXTS = (".onnx", ".pt")


class ModelManagerDialog(QDialog):
    """模型管理对话框：左右分栏，Nano 模型 + PC 本地模型"""

    local_model_selected = pyqtSignal(str)   # 本地模型路径已选择
    reconnect_requested = pyqtSignal()       # 请求重新连接下位机

    def __init__(self, tcp, parent=None, default_model: str = "",
                 default_model_dir: str = ""):
        super().__init__(parent)
        self.tcp = tcp
        self._default_model = default_model
        self._default_model_dir = default_model_dir or ""
        self.setWindowTitle("模型管理")
        self.setMinimumSize(1100, 720)
        self.resize(1280, 760)
        self._models = []
        self._active = ""
        self._build_ui()
        self._connect_signals()
        self._restore_local_model()
        self._update_source_status()
        self.refresh_list()

    # ---------- UI ----------
    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 14, 16, 14)
        root.setSpacing(12)

        # 顶部推理源状态
        self.lbl_status = QLabel("推理源：检测中...")
        self.lbl_status.setStyleSheet(
            "color:#94a3b8; font-size:17px; background:transparent;")
        root.addWidget(self.lbl_status)

        # 左右分栏
        splitter = QSplitter(Qt.Horizontal)
        splitter.setHandleWidth(8)
        splitter.setStyleSheet("QSplitter::handle { background: #1f2937; }")

        # -------- 左侧：Nano 模型 --------
        left = self._build_nano_panel()
        splitter.addWidget(left)

        # -------- 右侧：本地模型 --------
        right = self._build_local_panel()
        splitter.addWidget(right)

        splitter.setSizes([760, 460])  # 左宽 760，右宽 460
        root.addWidget(splitter, 1)

        # 底部关闭按钮
        btns = QHBoxLayout()
        btns.addStretch()
        btn_close = QPushButton("\u2715  关闭")  # ✕
        btn_close.setFixedSize(130, 40)
        btn_close.setCursor(Qt.PointingHandCursor)
        btn_close.setStyleSheet(
            "QPushButton { background:#334155; color:#f1f5f9; border:none; "
            "border-radius:5px; padding:0 18px; font-size:15px; font-weight:500; }"
            "QPushButton:hover { background:#475569; }"
            "QPushButton:pressed { background:#1e293b; }")
        btn_close.clicked.connect(self.accept)
        btns.addWidget(btn_close)
        root.addLayout(btns)

    def _build_nano_panel(self) -> QWidget:
        panel = QFrame()
        panel.setFrameShape(QFrame.StyledPanel)
        panel.setStyleSheet("QFrame { border: 1px solid #1f2937; border-radius: 6px; background: #0f172a; }")
        lay = QVBoxLayout(panel)
        lay.setContentsMargins(14, 12, 14, 12)
        lay.setSpacing(12)

        # 标题
        title = QLabel("下位机（Nano）模型")
        title.setStyleSheet(
            "border-left:4px solid #2563eb; padding-left:10px;"
            "font-size:18px; font-weight:600; color:#f1f5f9; background:transparent;")
        lay.addWidget(title)

        # 连接状态 + 按钮行
        top_row = QHBoxLayout()
        self.lbl_nano_conn = QLabel("状态：未连接")
        self.lbl_nano_conn.setStyleSheet(
            "color:#64748b; font-size:15px; background:transparent;")
        top_row.addWidget(self.lbl_nano_conn)
        top_row.addStretch()

        self.btn_reconnect = QPushButton("\u21bb  重新连接")  # ↻
        self.btn_reconnect.setFixedHeight(38)
        self.btn_reconnect.setCursor(Qt.PointingHandCursor)
        self.btn_reconnect.setStyleSheet(
            "QPushButton { background:#1d4ed8; color:#ffffff; border:none; "
            "border-radius:5px; padding:0 14px; font-size:15px; font-weight:500; }"
            "QPushButton:hover { background:#2563eb; }"
            "QPushButton:pressed { background:#1e40af; }"
            "QPushButton:disabled { background:#334155; color:#94a3b8; }")
        self.btn_reconnect.clicked.connect(self._on_reconnect)
        top_row.addWidget(self.btn_reconnect)
        lay.addLayout(top_row)

        # 模型表格
        self.table = QTableWidget()
        self.table.setColumnCount(4)
        self.table.setHorizontalHeaderLabels(["模型名称", "状态", "模型质量", "说明"])
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(40)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setShowGrid(False)
        self.table.setStyleSheet("font-size:16px; background:#0b1120;")
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(3, QHeaderView.Stretch)
        self.table.itemSelectionChanged.connect(self._on_select)
        lay.addWidget(self.table, 1)

        # 操作按钮 + 状态
        btn_row = QHBoxLayout()
        btn_row.setSpacing(10)
        self.btn_refresh = QPushButton("\u21bb  刷新清单")  # ↻
        self.btn_refresh.setFixedHeight(38)
        self.btn_refresh.setCursor(Qt.PointingHandCursor)
        self.btn_refresh.setStyleSheet(
            "QPushButton { background:#0f172a; color:#e2e8f0; border:1px solid #334155; "
            "border-radius:5px; padding:0 14px; font-size:15px; font-weight:500; }"
            "QPushButton:hover { background:#1e293b; border-color:#475569; }"
            "QPushButton:pressed { background:#0b1120; }"
            "QPushButton:disabled { background:#1e293b; color:#64748b; }")
        self.btn_refresh.clicked.connect(self.refresh_list)

        self.btn_load = QPushButton("\u2713  切换所选模型")  # ✓
        self.btn_load.setFixedHeight(38)
        self.btn_load.setCursor(Qt.PointingHandCursor)
        self.btn_load.setEnabled(False)
        self.btn_load.setStyleSheet(
            "QPushButton { background:#15803d; color:#ffffff; border:none; "
            "border-radius:5px; padding:0 14px; font-size:15px; font-weight:500; }"
            "QPushButton:hover { background:#16a34a; }"
            "QPushButton:pressed { background:#14532d; }"
            "QPushButton:disabled { background:#334155; color:#94a3b8; }")
        self.btn_load.clicked.connect(self._on_load_selected)
        btn_row.addWidget(self.btn_refresh)
        btn_row.addWidget(self.btn_load)
        btn_row.addStretch()
        self.lbl_nano = QLabel("")
        self.lbl_nano.setStyleSheet("color:#64748b; font-size:15px; background:transparent;")
        btn_row.addWidget(self.lbl_nano)
        lay.addLayout(btn_row)

        hint = QLabel("提示：连接下位机后刷新清单，选中模型点击「切换所选模型」即可生效")
        hint.setStyleSheet("color:#64748b; font-size:14px; background:transparent;")
        lay.addWidget(hint)
        return panel

    def _build_local_panel(self) -> QWidget:
        panel = QFrame()
        panel.setFrameShape(QFrame.StyledPanel)
        panel.setStyleSheet("QFrame { border: 1px solid #1f2937; border-radius: 6px; background: #0f172a; }")
        lay = QVBoxLayout(panel)
        lay.setContentsMargins(14, 12, 14, 12)
        lay.setSpacing(12)

        # 标题
        title = QLabel("PC 本地模型")
        title.setStyleSheet(
            "border-left:4px solid #22c55e; padding-left:10px;"
            "font-size:18px; font-weight:600; color:#f1f5f9; background:transparent;")
        lay.addWidget(title)

        subtitle = QLabel("无需 Nano 在线，开始检测时启用本地推理")
        subtitle.setStyleSheet("color:#64748b; font-size:15px; background:transparent;")
        lay.addWidget(subtitle)

        # 当前路径 + 浏览
        path_row = QHBoxLayout()
        path_row.setSpacing(8)
        self.edit_local = QLineEdit()
        self.edit_local.setPlaceholderText("当前选中的本地模型路径")
        self.edit_local.setFixedHeight(38)
        self.edit_local.setReadOnly(True)
        btn_browse = QPushButton("\ud83d\udcc1  浏览...")  # 📁
        btn_browse.setFixedHeight(38)
        btn_browse.setCursor(Qt.PointingHandCursor)
        btn_browse.setStyleSheet(
            "QPushButton { background:#0f172a; color:#e2e8f0; border:1px solid #334155; "
            "border-radius:5px; padding:0 14px; font-size:15px; font-weight:500; }"
            "QPushButton:hover { background:#1e293b; border-color:#475569; }"
            "QPushButton:pressed { background:#0b1120; }")
        btn_browse.clicked.connect(self._on_browse)
        path_row.addWidget(self.edit_local, 1)
        path_row.addWidget(btn_browse)
        lay.addLayout(path_row)

        # 扫描按钮
        scan_row = QHBoxLayout()
        scan_row.setSpacing(10)
        self.btn_scan = QPushButton("\ud83d\udd0d  扫描目录")  # 🔍
        self.btn_scan.setFixedHeight(38)
        self.btn_scan.setCursor(Qt.PointingHandCursor)
        self.btn_scan.setStyleSheet(
            "QPushButton { background:#0f172a; color:#e2e8f0; border:1px solid #334155; "
            "border-radius:5px; padding:0 14px; font-size:15px; font-weight:500; }"
            "QPushButton:hover { background:#1e293b; border-color:#475569; }"
            "QPushButton:pressed { background:#0b1120; }")
        self.btn_scan.clicked.connect(self._on_scan_dir)

        self.btn_auto_scan = QPushButton("\u2728  自动识别")  # ✨
        self.btn_auto_scan.setFixedHeight(38)
        self.btn_auto_scan.setCursor(Qt.PointingHandCursor)
        self.btn_auto_scan.setToolTip("扫描上次模型目录及常见模型目录")
        self.btn_auto_scan.setStyleSheet(
            "QPushButton { background:#2563eb; color:#ffffff; border:none; "
            "border-radius:5px; padding:0 14px; font-size:15px; font-weight:500; }"
            "QPushButton:hover { background:#3b82f6; }"
            "QPushButton:pressed { background:#1d4ed8; }")
        self.btn_auto_scan.clicked.connect(self._on_auto_scan)
        scan_row.addWidget(self.btn_scan)
        scan_row.addWidget(self.btn_auto_scan)
        scan_row.addStretch()
        self.lbl_local = QLabel("未扫描")
        self.lbl_local.setStyleSheet("color:#64748b; font-size:15px; background:transparent;")
        scan_row.addWidget(self.lbl_local)
        lay.addLayout(scan_row)

        # 模型列表
        self.list_local = QListWidget()
        self.list_local.setStyleSheet("font-size:16px; background:#0b1120;")
        self.list_local.setMinimumHeight(180)
        self.list_local.itemClicked.connect(self._on_local_item_clicked)
        self.list_local.itemDoubleClicked.connect(self._on_use_local)
        lay.addWidget(self.list_local, 1)

        # 设为本地推理模型
        use_row = QHBoxLayout()
        use_row.addStretch()
        self.btn_use = QPushButton("\u2713  设为本地推理模型")  # ✓
        self.btn_use.setFixedHeight(40)
        self.btn_use.setCursor(Qt.PointingHandCursor)
        self.btn_use.setStyleSheet(
            "QPushButton { background:#16a34a; color:#ffffff; border:none; "
            "border-radius:5px; padding:0 18px; font-size:15px; font-weight:600; }"
            "QPushButton:hover { background:#22c55e; }"
            "QPushButton:pressed { background:#15803d; }"
            "QPushButton:disabled { background:#334155; color:#94a3b8; }")
        self.btn_use.clicked.connect(self._on_use_local)
        use_row.addWidget(self.btn_use)
        lay.addLayout(use_row)

        hint = QLabel("提示：选中列表中的模型或浏览文件后，点击上方按钮设为本地推理模型")
        hint.setStyleSheet("color:#64748b; font-size:14px; background:transparent;")
        lay.addWidget(hint)
        return panel

    def _connect_signals(self):
        self.tcp.model_list_received.connect(self._on_list_received)
        self.tcp.model_load_received.connect(self._on_load_received)

    # ---------- 推理源状态 ----------
    def _update_source_status(self):
        if self.tcp.is_connected:
            self.lbl_status.setText("推理源：下位机已连接，优先使用下位机推理")
            self.lbl_status.setStyleSheet(
                "color:#22c55e; font-size:17px; background:transparent;")
            self.lbl_nano_conn.setText("状态：已连接")
            self.lbl_nano_conn.setStyleSheet(
                "color:#22c55e; font-size:15px; background:transparent;")
        else:
            host = getattr(self.tcp, "host", "192.168.1.101")
            port = getattr(self.tcp, "port", 8888)
            self.lbl_status.setText(
                f"推理源：下位机未连接（{host}:{port}），可选用本地模型")
            self.lbl_status.setStyleSheet(
                "color:#94a3b8; font-size:17px; background:transparent;")
            self.lbl_nano_conn.setText("状态：未连接")
            self.lbl_nano_conn.setStyleSheet(
                "color:#ef4444; font-size:15px; background:transparent;")

    # ---------- Nano 清单 ----------
    def refresh_list(self):
        self._update_source_status()
        if not self.tcp.is_connected:
            self.lbl_nano.setText("未连接下位机")
            self.table.setRowCount(0)
            return
        self.lbl_nano.setText("获取清单中...")
        self.tcp.request_model_list()

    def _on_list_received(self, payload: dict):
        self._models = payload.get("models", [])
        self._active = payload.get("active", "")
        self.table.setRowCount(0)
        for m in self._models:
            name = m.get("name", "") if isinstance(m, dict) else str(m)
            active = name == self._active
            q_label, q_color, q_tip = model_quality(name)
            row = self.table.rowCount()
            self.table.insertRow(row)
            self.table.setItem(row, 0, QTableWidgetItem(name))
            status_item = QTableWidgetItem("● 当前激活" if active else "")
            if active:
                status_item.setForeground(_qcolor("#22c55e"))
            self.table.setItem(row, 1, status_item)
            quality_item = QTableWidgetItem(q_label)
            quality_item.setToolTip(q_tip)
            quality_item.setForeground(_qcolor(q_color))
            quality_item.setTextAlignment(Qt.AlignCenter)
            self.table.setItem(row, 2, quality_item)
            self.table.setItem(row, 3, QTableWidgetItem(
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
        self.lbl_nano.setText(f"请求切换: {name}")

    def _on_load_received(self, payload: dict):
        ok = payload.get("ok", False)
        msg = payload.get("message", payload.get("msg", ""))
        name = payload.get("model", "")
        if ok:
            self._active = name
            self.lbl_nano.setText(f"切换成功: {name}")
            self.refresh_list()
        else:
            self.lbl_nano.setText(f"切换失败: {msg or name}")
            QMessageBox.warning(self, "模型切换", f"切换失败: {msg or name}")

    def _on_reconnect(self):
        self.lbl_nano.setText("正在重新连接...")
        self.reconnect_requested.emit()

    # ---------- 本地模型 ----------
    def _on_browse(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "选择本地模型", self._default_model_dir,
            "模型文件 (*.onnx *.pt)")
        if path:
            self.edit_local.setText(path)
            self._add_local_item(path)

    def _on_scan_dir(self):
        d = QFileDialog.getExistingDirectory(
            self, "扫描模型目录", self._default_model_dir)
        if d:
            self._scan_directory(d)

    def _on_auto_scan(self):
        """自动识别常见模型目录和上次模型目录中的 .onnx/.pt 文件"""
        self.list_local.clear()
        self.lbl_local.setText("自动识别中...")
        QApplication.processEvents()

        if _auto_scan is None:
            QMessageBox.warning(self, "自动识别", "自动扫描模块未加载，请检查 tools/auto_scan_models.py 是否存在。")
            self.lbl_local.setText("自动识别失败")
            return

        models = _auto_scan(max_depth=5)
        for m in models:
            self._add_local_item(m["path"])

        self.lbl_local.setText(f"自动识别到 {len(models)} 个模型")
        if not models:
            QMessageBox.information(
                self, "自动识别",
                "在常见目录中未找到 .onnx / .pt 模型文件。\n\n"
                "请使用「扫描目录」手动选择模型所在文件夹。")

    def _scan_directory(self, root: str, add_to_list=True, clear_first=True) -> int:
        """扫描目录下的模型文件，默认递归深度 5，避免卡死"""
        if clear_first and add_to_list:
            self.list_local.clear()
        self.lbl_local.setText("扫描中...")
        QApplication.processEvents()

        if _scan_models is None:
            QMessageBox.warning(self, "扫描目录", "自动扫描模块未加载，请检查 tools/auto_scan_models.py 是否存在。")
            self.lbl_local.setText("扫描失败")
            return 0

        models = _scan_models([root], max_depth=5)
        if add_to_list:
            for m in models:
                self._add_local_item(m["path"])
            self.lbl_local.setText(f"扫描到 {len(models)} 个模型")
        return len(models)

    def _add_local_item(self, path: str):
        """将单个文件加入本地模型列表并选中"""
        for i in range(self.list_local.count()):
            if self.list_local.item(i).data(Qt.UserRole) == path:
                self.list_local.setCurrentRow(i)
                return
        item = QListWidgetItem(os.path.basename(path))
        item.setData(Qt.UserRole, path)
        item.setToolTip(path)
        self.list_local.addItem(item)
        self.list_local.setCurrentRow(self.list_local.count() - 1)

    def _on_local_item_clicked(self, item: QListWidgetItem):
        path = item.data(Qt.UserRole)
        if path:
            self.edit_local.setText(path)

    def _restore_local_model(self):
        if self._default_model and os.path.isfile(self._default_model):
            self.edit_local.setText(self._default_model)
            self._add_local_item(self._default_model)
            self.lbl_local.setText("已恢复上次选中的本地模型")

    def _on_use_local(self):
        path = self.edit_local.text().strip()
        if not path:
            # 尝试从列表当前选中项取
            item = self.list_local.currentItem()
            if item:
                path = item.data(Qt.UserRole) or ""
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
        self.lbl_status.setStyleSheet(
            "color:#22c55e; font-size:17px; background:transparent;")
        QMessageBox.information(
            self, "模型管理",
            f"已设为本地推理模型:\n{os.path.basename(path)}")

    # ---------- 关闭时 ----------
    def closeEvent(self, event):
        try:
            self.tcp.model_list_received.disconnect(self._on_list_received)
            self.tcp.model_load_received.disconnect(self._on_load_received)
        except Exception:
            pass
        super().closeEvent(event)


def _qcolor(hex_str: str):
    from PyQt5.QtGui import QColor
    return QColor(hex_str)

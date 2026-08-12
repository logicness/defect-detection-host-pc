# -*- coding: utf-8 -*-
"""
模型管理对话框（RK3568 版）v3
==========================
双 Tab 设计：
- 「选择模型」：汇总下位机模型 + 本地模型库，查看详情并一键切换/加载当前模型
- 「模型管理」：管理下位机模型与本地模型库（刷新/扫描/添加/删除/配置）

依赖：core.tcp_client.TCPClient（model_list_received / model_load_received）
"""
import os
import sys

from PyQt5.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QLineEdit,
    QTableWidget, QTableWidgetItem, QHeaderView, QAbstractItemView,
    QFileDialog, QMessageBox, QSplitter, QListWidget, QListWidgetItem,
    QFrame, QWidget, QApplication, QTabWidget, QProgressBar
)
from PyQt5.QtCore import Qt, pyqtSignal

from components.model_library import (
    score_quality, model_quality, load_library, save_library
)
from components.model_info import (
    model_tags, extract_dataset, extract_classes,
    is_pretrained, find_library_item
)

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


def _info_popup(parent, title: str, text: str):
    """信息弹窗（无头环境 offscreen 下跳过，避免崩溃）"""
    if QApplication.platformName() != "offscreen":
        QMessageBox.information(parent, title, text)


def _shorten_path(path: str, max_len: int = 60) -> str:
    """路径太长时保留头部和尾部"""
    if len(path) <= max_len:
        return path
    head = path[:22]
    tail = path[-(max_len - 25):]
    return f"{head}...{tail}"


def _basename(name: str) -> str:
    """取下位机模型名称中的 basename（去掉目录前缀）"""
    return name.replace("\\", "/").rsplit("/", 1)[-1]


class ModelManagerDialog(QDialog):
    """模型管理对话框 v3

    入口：
    - 「加载模型」按钮 → 打开本对话框并默认显示「选择模型」Tab
    - 「模型管理」按钮 → 打开本对话框并默认显示「模型管理」Tab

    信号：
    - local_model_selected(str)：本地模型被设为当前推理模型
    - local_model_deleted(str)：本地模型被从库/磁盘删除
    - reconnect_requested()：请求主程序重新连接下位机
    """

    local_model_selected = pyqtSignal(str)   # 本地模型路径已选择（加载）
    local_model_deleted = pyqtSignal(str)    # 本地模型已删除（从库移除或删文件）
    reconnect_requested = pyqtSignal()       # 请求重新连接下位机

    def __init__(self, tcp, parent=None, default_model: str = "",
                 default_model_dir: str = "", open_tab: str = "select"):
        """
        open_tab: "select" | "manage"，决定默认显示哪个 Tab
        """
        super().__init__(parent)
        self.tcp = tcp
        self._default_model = default_model
        self._default_model_dir = default_model_dir or ""
        self._open_tab = open_tab
        self.setWindowTitle("模型管理")
        self.setMinimumSize(1200, 780)
        self.resize(1320, 820)
        self._models = []          # 下位机原始模型数据
        self._active_nano = ""     # 当前激活的下位机模型
        self._selected_path = ""   # 当前在「选择模型」列表中选中的路径
        self._selected_source = "" # "nano" | "local"
        self._build_ui()
        self._connect_signals()
        self._update_source_status()
        self._refresh_all()

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

        # Tab 容器
        self.tabs = QTabWidget()
        self.tabs.setStyleSheet(
            "QTabWidget::pane { border:1px solid #1f2937; border-radius:6px; "
            "background:#0b1120; top:-1px; }"
            "QTabBar::tab { background:#0f172a; color:#94a3b8; padding:10px 24px; "
            "font-size:16px; border:1px solid #1f2937; border-bottom:none; "
            "border-top-left-radius:6px; border-top-right-radius:6px; margin-right:4px; }"
            "QTabBar::tab:selected { background:#1e293b; color:#f1f5f9; font-weight:600; }"
            "QTabBar::tab:hover { background:#1e293b; color:#e2e8f0; }")

        # Tab 1: 选择模型
        self.tab_select = self._build_select_tab()
        self.tabs.addTab(self.tab_select, "选择模型")

        # Tab 2: 模型管理
        self.tab_manage = self._build_manage_tab()
        self.tabs.addTab(self.tab_manage, "模型管理")

        if self._open_tab == "manage":
            self.tabs.setCurrentIndex(1)
        else:
            self.tabs.setCurrentIndex(0)
        root.addWidget(self.tabs, 1)

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

    # ---------- Tab 1: 选择模型 ----------
    def _build_select_tab(self) -> QWidget:
        w = QWidget()
        lay = QHBoxLayout(w)
        lay.setContentsMargins(14, 14, 14, 14)
        lay.setSpacing(14)

        # 左侧：模型列表
        left = QFrame()
        left.setStyleSheet("QFrame { border:1px solid #1f2937; border-radius:6px; background:#0f172a; }")
        llay = QVBoxLayout(left)
        llay.setContentsMargins(12, 12, 12, 12)
        llay.setSpacing(10)

        title = QLabel("可用模型")
        title.setStyleSheet(
            "border-left:4px solid #2563eb; padding-left:10px;"
            "font-size:18px; font-weight:600; color:#f1f5f9; background:transparent;")
        llay.addWidget(title)

        sub = QLabel("来源：下位机已同步模型 + 本地模型库")
        sub.setStyleSheet("color:#64748b; font-size:14px; background:transparent;")
        llay.addWidget(sub)

        self.tbl_select = QTableWidget()
        self.tbl_select.setColumnCount(7)
        self.tbl_select.setHorizontalHeaderLabels(
            ["来源", "模型名称", "数据集", "类别", "状态", "质量", "说明"])
        self.tbl_select.verticalHeader().setVisible(False)
        self.tbl_select.verticalHeader().setDefaultSectionSize(42)
        self.tbl_select.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.tbl_select.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.tbl_select.setSelectionMode(QAbstractItemView.SingleSelection)
        self.tbl_select.setShowGrid(False)
        self.tbl_select.setStyleSheet(
            "QTableWidget { font-size:16px; background:#0b1120; border:none; }"
            "QHeaderView::section { background:#1e293b; color:#e2e8f0; padding:8px; "
            "font-size:15px; font-weight:600; border:none; }")
        self.tbl_select.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self.tbl_select.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.tbl_select.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self.tbl_select.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeToContents)
        self.tbl_select.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeToContents)
        self.tbl_select.horizontalHeader().setSectionResizeMode(5, QHeaderView.ResizeToContents)
        self.tbl_select.horizontalHeader().setSectionResizeMode(6, QHeaderView.Stretch)
        self.tbl_select.itemSelectionChanged.connect(self._on_select_model)
        llay.addWidget(self.tbl_select, 1)

        hint = QLabel("提示：单击选中模型，右侧查看详情并加载/切换")
        hint.setStyleSheet("color:#64748b; font-size:14px; background:transparent;")
        llay.addWidget(hint)
        lay.addWidget(left, 2)

        # 右侧：模型详情
        right = QFrame()
        right.setStyleSheet("QFrame { border:1px solid #1f2937; border-radius:6px; background:#0f172a; }")
        rlay = QVBoxLayout(right)
        rlay.setContentsMargins(14, 14, 14, 14)
        rlay.setSpacing(12)

        t2 = QLabel("模型详情")
        t2.setStyleSheet(
            "border-left:4px solid #22c55e; padding-left:10px;"
            "font-size:18px; font-weight:600; color:#f1f5f9; background:transparent;")
        rlay.addWidget(t2)

        self.detail_name = QLabel("未选择模型")
        self.detail_name.setStyleSheet(
            "font-size:18px; font-weight:600; color:#f1f5f9; background:transparent;")
        self.detail_name.setWordWrap(True)
        rlay.addWidget(self.detail_name)

        self.detail_source = QLabel("来源：--")
        self.detail_source.setStyleSheet("font-size:15px; color:#94a3b8; background:transparent;")
        rlay.addWidget(self.detail_source)

        self.detail_quality = QLabel("质量：--")
        self.detail_quality.setStyleSheet("font-size:16px; font-weight:600; background:transparent;")
        rlay.addWidget(self.detail_quality)

        self.detail_dataset = QLabel("数据集：--")
        self.detail_dataset.setStyleSheet("font-size:15px; color:#94a3b8; background:transparent;")
        rlay.addWidget(self.detail_dataset)

        self.detail_classes = QLabel("类别数：--")
        self.detail_classes.setStyleSheet("font-size:15px; color:#94a3b8; background:transparent;")
        rlay.addWidget(self.detail_classes)

        self.detail_recommend = QLabel("")
        self.detail_recommend.setStyleSheet(
            "font-size:15px; font-weight:600; color:#22c55e; background:transparent;")
        rlay.addWidget(self.detail_recommend)

        self.detail_score = QProgressBar()
        self.detail_score.setRange(0, 100)
        self.detail_score.setValue(0)
        self.detail_score.setTextVisible(True)
        self.detail_score.setFormat("综合得分 %p")
        self.detail_score.setStyleSheet(
            "QProgressBar { border:1px solid #334155; border-radius:4px; text-align:center; "
            "color:#f1f5f9; font-size:14px; background:#0b1120; height:22px; }"
            "QProgressBar::chunk { border-radius:4px; background:#2563eb; }")
        rlay.addWidget(self.detail_score)

        self.detail_path = QLabel("路径：--")
        self.detail_path.setStyleSheet("font-size:14px; color:#64748b; background:transparent;")
        self.detail_path.setWordWrap(True)
        rlay.addWidget(self.detail_path)

        self.detail_size = QLabel("大小：--")
        self.detail_size.setStyleSheet("font-size:14px; color:#64748b; background:transparent;")
        rlay.addWidget(self.detail_size)

        self.detail_desc = QLabel("说明：--")
        self.detail_desc.setStyleSheet("font-size:14px; color:#94a3b8; background:transparent;")
        self.detail_desc.setWordWrap(True)
        rlay.addWidget(self.detail_desc)

        rlay.addStretch()

        self.btn_select_load = QPushButton("\u25b6  加载为当前模型")  # ▶
        self.btn_select_load.setFixedHeight(44)
        self.btn_select_load.setCursor(Qt.PointingHandCursor)
        self.btn_select_load.setEnabled(False)
        self.btn_select_load.setStyleSheet(
            "QPushButton { background:#16a34a; color:#ffffff; border:none; "
            "border-radius:5px; padding:0 20px; font-size:16px; font-weight:600; }"
            "QPushButton:hover { background:#22c55e; }"
            "QPushButton:pressed { background:#15803d; }"
            "QPushButton:disabled { background:#334155; color:#94a3b8; }")
        self.btn_select_load.clicked.connect(self._on_select_load)
        rlay.addWidget(self.btn_select_load)

        lay.addWidget(right, 1)
        return w

    # ---------- Tab 2: 模型管理 ----------
    def _build_manage_tab(self) -> QWidget:
        splitter = QSplitter(Qt.Horizontal)
        splitter.setHandleWidth(8)
        splitter.setStyleSheet("QSplitter::handle { background:#1f2937; }")

        # 左：下位机模型管理
        left = self._build_nano_panel()
        splitter.addWidget(left)

        # 右：本地模型库管理
        right = self._build_local_panel()
        splitter.addWidget(right)

        splitter.setSizes([640, 560])
        return splitter

    def _build_nano_panel(self) -> QWidget:
        panel = QFrame()
        panel.setStyleSheet("QFrame { border:1px solid #1f2937; border-radius:6px; background:#0f172a; }")
        lay = QVBoxLayout(panel)
        lay.setContentsMargins(14, 12, 14, 12)
        lay.setSpacing(12)

        title = QLabel("下位机模型管理")
        title.setStyleSheet(
            "border-left:4px solid #2563eb; padding-left:10px;"
            "font-size:18px; font-weight:600; color:#f1f5f9; background:transparent;")
        lay.addWidget(title)

        top = QHBoxLayout()
        self.lbl_nano_conn = QLabel("状态：未连接")
        self.lbl_nano_conn.setStyleSheet(
            "color:#64748b; font-size:15px; background:transparent;")
        top.addWidget(self.lbl_nano_conn)
        top.addStretch()

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
        top.addWidget(self.btn_reconnect)
        lay.addLayout(top)

        self.table_nano = QTableWidget()
        self.table_nano.setColumnCount(4)
        self.table_nano.setHorizontalHeaderLabels(["模型名称", "状态", "模型质量", "说明"])
        self.table_nano.verticalHeader().setVisible(False)
        self.table_nano.verticalHeader().setDefaultSectionSize(42)
        self.table_nano.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table_nano.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table_nano.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table_nano.setShowGrid(False)
        self.table_nano.setStyleSheet(
            "QTableWidget { font-size:16px; background:#0b1120; border:none; }"
            "QHeaderView::section { background:#1e293b; color:#e2e8f0; padding:8px; "
            "font-size:15px; font-weight:600; border:none; }")
        self.table_nano.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.table_nano.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self.table_nano.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self.table_nano.horizontalHeader().setSectionResizeMode(3, QHeaderView.Stretch)
        self.table_nano.itemSelectionChanged.connect(self._on_nano_select)
        lay.addWidget(self.table_nano, 1)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(10)
        self.btn_refresh = QPushButton("\u21bb  刷新清单")  # ↻
        self.btn_refresh.setFixedHeight(38)
        self.btn_refresh.setCursor(Qt.PointingHandCursor)
        self.btn_refresh.setStyleSheet(
            "QPushButton { background:#0f172a; color:#e2e8f0; border:1px solid #334155; "
            "border-radius:5px; padding:0 14px; font-size:15px; font-weight:500; }"
            "QPushButton:hover { background:#1e293b; border-color:#475569; }"
            "QPushButton:pressed { background:#0b1120; }")
        self.btn_refresh.clicked.connect(self.refresh_nano_list)

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

        hint = QLabel("提示：连接下位机后刷新清单，选中模型点击「切换所选模型」生效")
        hint.setStyleSheet("color:#64748b; font-size:14px; background:transparent;")
        lay.addWidget(hint)
        return panel

    def _build_local_panel(self) -> QWidget:
        panel = QFrame()
        panel.setStyleSheet("QFrame { border:1px solid #1f2937; border-radius:6px; background:#0f172a; }")
        lay = QVBoxLayout(panel)
        lay.setContentsMargins(14, 12, 14, 12)
        lay.setSpacing(12)

        title = QLabel("本地模型库管理")
        title.setStyleSheet(
            "border-left:4px solid #22c55e; padding-left:10px;"
            "font-size:18px; font-weight:600; color:#f1f5f9; background:transparent;")
        lay.addWidget(title)

        sub = QLabel("添加、扫描或删除 PC 本地 .onnx / .pt 模型")
        sub.setStyleSheet("color:#64748b; font-size:14px; background:transparent;")
        lay.addWidget(sub)

        # 路径浏览
        path_row = QHBoxLayout()
        self.edit_local = QLineEdit()
        self.edit_local.setPlaceholderText("选中的本地模型路径")
        self.edit_local.setFixedHeight(38)
        self.edit_local.setReadOnly(True)
        self.edit_local.setStyleSheet(
            "QLineEdit { background:#0b1120; color:#e2e8f0; border:1px solid #334155; "
            "border-radius:5px; padding:0 10px; font-size:14px; }")
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

        # 扫描按钮行
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
        self.btn_auto_scan.setToolTip("扫描常见模型目录及上次模型目录")
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

        # 本地模型列表
        self.list_local = QListWidget()
        self.list_local.setStyleSheet(
            "QListWidget { font-size:16px; background:#0b1120; border:1px solid #1f2937; "
            "border-radius:5px; padding:4px; }"
            "QListWidget::item { padding:8px; border-bottom:1px solid #1f2937; }"
            "QListWidget::item:selected { background:#1e293b; color:#f1f5f9; }")
        self.list_local.setMinimumHeight(180)
        self.list_local.itemClicked.connect(self._on_local_item_clicked)
        lay.addWidget(self.list_local, 1)

        # 管理按钮
        mgr_row = QHBoxLayout()
        mgr_row.setSpacing(10)
        self.btn_add_lib = QPushButton("\u2606  添加到模型库")  # ☆
        self.btn_add_lib.setFixedHeight(38)
        self.btn_add_lib.setCursor(Qt.PointingHandCursor)
        self.btn_add_lib.setToolTip("把选中模型持久化到模型库")
        self.btn_add_lib.setStyleSheet(
            "QPushButton { background:#0f172a; color:#e2e8f0; border:1px solid #334155; "
            "border-radius:5px; padding:0 14px; font-size:15px; font-weight:500; }"
            "QPushButton:hover { background:#1e293b; border-color:#475569; }"
            "QPushButton:pressed { background:#0b1120; }"
            "QPushButton:disabled { background:#1e293b; color:#64748b; border-color:#334155; }")
        self.btn_add_lib.clicked.connect(self._on_add_to_library)

        self.btn_edit_info = QPushButton("\u270e  编辑信息")  # ✎
        self.btn_edit_info.setFixedHeight(38)
        self.btn_edit_info.setCursor(Qt.PointingHandCursor)
        self.btn_edit_info.setToolTip("修改模型库中该模型的显示名称和备注")
        self.btn_edit_info.setStyleSheet(
            "QPushButton { background:#0f172a; color:#e2e8f0; border:1px solid #334155; "
            "border-radius:5px; padding:0 14px; font-size:15px; font-weight:500; }"
            "QPushButton:hover { background:#1e293b; border-color:#475569; }"
            "QPushButton:pressed { background:#0b1120; }")
        self.btn_edit_info.clicked.connect(self._on_edit_library_info)

        self.btn_del = QPushButton("\ud83d\uddd1  删除")  # 🗑
        self.btn_del.setFixedHeight(38)
        self.btn_del.setCursor(Qt.PointingHandCursor)
        self.btn_del.setToolTip("从模型库移除（可选同时删除文件）")
        self.btn_del.setStyleSheet(
            "QPushButton { background:#0f172a; color:#ef4444; border:1px solid #7f1d1d; "
            "border-radius:5px; padding:0 14px; font-size:15px; font-weight:500; }"
            "QPushButton:hover { background:#1e293b; border-color:#dc2626; }"
            "QPushButton:pressed { background:#0b1120; }")
        self.btn_del.clicked.connect(self._on_delete_local)
        mgr_row.addWidget(self.btn_add_lib)
        mgr_row.addWidget(self.btn_edit_info)
        mgr_row.addWidget(self.btn_del)
        mgr_row.addStretch()
        lay.addLayout(mgr_row)

        hint = QLabel("提示：「☆ 添加到模型库」保存常用模型；「✎ 编辑信息」可改显示名和备注；「删除」默认保留文件")
        hint.setStyleSheet("color:#64748b; font-size:14px; background:transparent;")
        lay.addWidget(hint)
        return panel

    def _connect_signals(self):
        self.tcp.model_list_received.connect(self._on_list_received)
        self.tcp.model_load_received.connect(self._on_load_received)

    # ---------- 公共状态 ----------
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

    def _refresh_all(self):
        self._restore_local_model()
        self.refresh_nano_list()
        self._refresh_select_table()

    # ---------- Tab 1: 选择模型 数据 ----------
    def _refresh_select_table(self):
        """汇总 Nano 模型 + 本地模型库，刷新「选择模型」列表"""
        self.tbl_select.setRowCount(0)

        # 本地模型库
        lib_items = []
        for e in load_library():
            p = e.get("path", "")
            if p and os.path.isfile(p):
                lib_items.append({"path": p, "source": "local", "in_lib": True})
        # 默认本地模型（若不在库中）
        if self._default_model and os.path.isfile(self._default_model):
            if not any(e["path"] == self._default_model for e in lib_items):
                lib_items.append({"path": self._default_model, "source": "local", "in_lib": False})

        # 下位机模型
        nano_items = []
        for m in self._models:
            name = m.get("name", "") if isinstance(m, dict) else str(m)
            nano_items.append({"name": name, "source": "nano", "data": m})

        # 先本地后 Nano
        for item in lib_items:
            self._insert_select_row(item)
        for item in nano_items:
            self._insert_select_row(item)

    def _insert_select_row(self, item: dict):
        row = self.tbl_select.rowCount()
        self.tbl_select.insertRow(row)

        if item.get("source") == "local":
            path = item["path"]
            tags = model_tags(path)
            lib_item = find_library_item(path)
            in_lib = lib_item is not None or item.get("in_lib", False)
            label = tags["quality_label"]
            score = tags["score"]
            color = tags["color"]
            tip = tags["tip"]
            source_text = "本地库" if in_lib else "本地"
            display_name = lib_item.get("display_name", "") if lib_item else ""
            name = display_name or os.path.basename(path)
            status = "✓ 已入库" if in_lib else ""
            dataset = tags["dataset"] or "未知"
            classes = f"{tags['classes']} 类" if tags["classes"] else "未知"
            if tags["is_recommended"]:
                status = "★ 推荐" + (" | " + status if status else "")
            if tags["is_pretrained"]:
                status = "预训练 backbone" + (" | " + status if status else "")
            desc = lib_item.get("note", "") if lib_item else _shorten_path(path, 45)
            self.tbl_select.setItem(row, 0, self._text_item(source_text))
            self.tbl_select.setItem(row, 1, self._text_item(name))
            self.tbl_select.setItem(row, 2, self._text_item(dataset))
            self.tbl_select.setItem(row, 3, self._text_item(classes))
            s_item = self._text_item(status)
            if "★ 推荐" in status:
                s_item.setForeground(_qcolor("#22c55e"))
            elif "预训练" in status:
                s_item.setForeground(_qcolor("#ef4444"))
            elif "✓ 已入库" in status:
                s_item.setForeground(_qcolor("#3b82f6"))
            self.tbl_select.setItem(row, 4, s_item)
            q_item = self._text_item(f"{label} ({score})" if score else label)
            q_item.setForeground(_qcolor(color))
            q_item.setToolTip(tip)
            q_item.setTextAlignment(Qt.AlignCenter)
            self.tbl_select.setItem(row, 5, q_item)
            d_item = self._text_item(desc)
            d_item.setToolTip(path)
            self.tbl_select.setItem(row, 6, d_item)
            # 绑定数据
            for c in range(7):
                it = self.tbl_select.item(row, c)
                it.setData(Qt.UserRole, path)
                it.setData(Qt.UserRole + 1, "local")
                it.setData(Qt.UserRole + 2, in_lib)
        else:
            m = item.get("data", {})
            name_full = m.get("name", "") if isinstance(m, dict) else str(m)
            name = _basename(name_full)
            active = name_full == self._active_nano
            q_label, q_color, q_tip = model_quality(name_full)
            tags = model_tags(name_full)
            source_text = "下位机"
            status = "当前激活" if active else ""
            dataset = tags["dataset"] or "未知"
            classes = f"{tags['classes']} 类" if tags["classes"] else "未知"
            desc = _shorten_path(name_full, 45)
            self.tbl_select.setItem(row, 0, self._text_item(source_text))
            self.tbl_select.setItem(row, 1, self._text_item(name))
            self.tbl_select.setItem(row, 2, self._text_item(dataset))
            self.tbl_select.setItem(row, 3, self._text_item(classes))
            s_item = self._text_item(status)
            if active:
                s_item.setForeground(_qcolor("#22c55e"))
            self.tbl_select.setItem(row, 4, s_item)
            q_item = self._text_item(q_label)
            q_item.setForeground(_qcolor(q_color))
            q_item.setToolTip(q_tip)
            q_item.setTextAlignment(Qt.AlignCenter)
            self.tbl_select.setItem(row, 5, q_item)
            d_item = self._text_item(desc)
            d_item.setToolTip(name_full)
            self.tbl_select.setItem(row, 6, d_item)
            for c in range(7):
                it = self.tbl_select.item(row, c)
                it.setData(Qt.UserRole, name_full)
                it.setData(Qt.UserRole + 1, "nano")

    def _text_item(self, text: str) -> QTableWidgetItem:
        item = QTableWidgetItem(text)
        item.setFlags(item.flags() & ~Qt.ItemIsEditable)
        return item

    def _on_select_model(self):
        row = self.tbl_select.currentRow()
        if row < 0:
            self._clear_detail()
            return
        item0 = self.tbl_select.item(row, 0)
        path_or_name = item0.data(Qt.UserRole) or ""
        source = item0.data(Qt.UserRole + 1) or ""
        self._selected_path = path_or_name
        self._selected_source = source
        self._show_detail(path_or_name, source)
        self.btn_select_load.setEnabled(True)

    def _clear_detail(self):
        self.detail_name.setText("未选择模型")
        self.detail_source.setText("来源：--")
        self.detail_quality.setText("质量：--")
        self.detail_quality.setStyleSheet("font-size:16px; font-weight:600; color:#94a3b8; background:transparent;")
        self.detail_dataset.setText("数据集：--")
        self.detail_classes.setText("类别数：--")
        self.detail_recommend.setText("")
        self.detail_score.setValue(0)
        self.detail_score.setStyleSheet(
            "QProgressBar { border:1px solid #334155; border-radius:4px; text-align:center; "
            "color:#f1f5f9; font-size:14px; background:#0b1120; height:22px; }"
            "QProgressBar::chunk { border-radius:4px; background:#64748b; }")
        self.detail_path.setText("路径：--")
        self.detail_path.setToolTip("")
        self.detail_size.setText("大小：--")
        self.detail_desc.setText("说明：--")
        self.btn_select_load.setEnabled(False)

    def _show_detail(self, path_or_name: str, source: str):
        if source == "local":
            path = path_or_name
            lib_item = find_library_item(path)
            tags = model_tags(path)
            display_name = lib_item.get("display_name", "") if lib_item else ""
            name = display_name or os.path.basename(path)
            self.detail_name.setText(name)
            self.detail_source.setText("来源：本地 PC 模型")
            label, score, color, tip = score_quality(path)
            self.detail_quality.setText(f"质量：{label}" + (f" （得分 {score}）" if score else ""))
            self.detail_quality.setStyleSheet(
                f"font-size:16px; font-weight:600; color:{color}; background:transparent;")
            self.detail_score.setValue(score if score else 0)
            self._set_score_color(score if score else 0)
            dataset = tags["dataset"] or "未知"
            classes = f"{tags['classes']} 类" if tags["classes"] else "未知"
            self.detail_dataset.setText(f"数据集：{dataset}")
            self.detail_classes.setText(f"类别数：{classes}")
            if tags["is_recommended"]:
                self.detail_recommend.setText("★ 推荐直接使用（高质量 + 已知数据集）")
                self.detail_recommend.setStyleSheet(
                    "font-size:15px; font-weight:600; color:#22c55e; background:transparent;")
            elif tags["is_pretrained"]:
                self.detail_recommend.setText("⚠ 预训练 backbone，不适合直接用于缺陷检测")
                self.detail_recommend.setStyleSheet(
                    "font-size:15px; font-weight:600; color:#ef4444; background:transparent;")
            else:
                self.detail_recommend.setText("")
            self.detail_path.setText(f"路径：{_shorten_path(path, 80)}")
            self.detail_path.setToolTip(path)
            try:
                size_mb = os.path.getsize(path) / 1e6
                self.detail_size.setText(f"大小：{size_mb:.2f} MB")
            except Exception:
                self.detail_size.setText("大小：--")
            note = lib_item.get("note", "") if lib_item else tip
            self.detail_desc.setText(f"说明：{note}")
            self.btn_select_load.setText("\u25b6  加载为本地推理模型")
        else:
            name_full = path_or_name
            name = _basename(name_full)
            tags = model_tags(name_full)
            self.detail_name.setText(name)
            self.detail_source.setText("来源：下位机（Nano）")
            label, color, tip = model_quality(name_full)
            # 下位机模型没有分数，用标签本身
            self.detail_quality.setText(f"质量：{label}")
            self.detail_quality.setStyleSheet(
                f"font-size:16px; font-weight:600; color:{color}; background:transparent;")
            self.detail_score.setValue(0)
            self._set_score_color(0)
            dataset = tags["dataset"] or "未知"
            classes = f"{tags['classes']} 类" if tags["classes"] else "未知"
            self.detail_dataset.setText(f"数据集：{dataset}")
            self.detail_classes.setText(f"类别数：{classes}")
            if tags["is_recommended"]:
                self.detail_recommend.setText("★ 推荐直接使用")
                self.detail_recommend.setStyleSheet(
                    "font-size:15px; font-weight:600; color:#22c55e; background:transparent;")
            else:
                self.detail_recommend.setText("")
            self.detail_path.setText(f"路径：{_shorten_path(name_full, 80)}")
            self.detail_path.setToolTip(name_full)
            self.detail_size.setText("大小：--")
            self.detail_desc.setText(f"说明：{tip}")
            self.btn_select_load.setText("\u25b6  切换为当前下位机模型")

    def _set_score_color(self, score: int):
        if score >= 80:
            color = "#22c55e"
        elif score >= 60:
            color = "#eab308"
        elif score > 0:
            color = "#ef4444"
        else:
            color = "#64748b"
        self.detail_score.setStyleSheet(
            f"QProgressBar {{ border:1px solid #334155; border-radius:4px; text-align:center; "
            f"color:#f1f5f9; font-size:14px; background:#0b1120; height:22px; }}"
            f"QProgressBar::chunk {{ border-radius:4px; background:{color}; }}")

    def _on_select_load(self):
        if self._selected_source == "local":
            path = self._selected_path
            if not path or not os.path.isfile(path):
                QMessageBox.warning(self, "加载模型", "选中的本地模型文件不存在")
                return
            self.local_model_selected.emit(path)
            self.lbl_status.setText(f"已加载本地模型：{os.path.basename(path)}")
            self.lbl_status.setStyleSheet(
                "color:#22c55e; font-size:17px; background:transparent;")
            QMessageBox.information(
                self, "加载模型",
                f"已加载为本地推理模型:\n{os.path.basename(path)}")
            self._default_model = path
            self._refresh_select_table()
        elif self._selected_source == "nano":
            name = self._selected_path
            self.tcp.load_model(name)
            self.lbl_status.setText(f"请求切换下位机模型: {_basename(name)}")
            self.lbl_status.setStyleSheet(
                "color:#eab308; font-size:17px; background:transparent;")
        else:
            QMessageBox.information(self, "加载模型", "请先选择一个模型")

    # ---------- Nano 清单 ----------
    def refresh_nano_list(self):
        self._update_source_status()
        if not self.tcp.is_connected:
            self.lbl_nano.setText("未连接下位机")
            self.table_nano.setRowCount(0)
            return
        self.lbl_nano.setText("获取清单中...")
        self.tcp.request_model_list()

    def _on_list_received(self, payload: dict):
        self._models = payload.get("models", [])
        self._active_nano = payload.get("active", "")
        self._update_source_status()

        # 更新 Nano 管理表格
        self.table_nano.setRowCount(0)
        for m in self._models:
            name_full = m.get("name", "") if isinstance(m, dict) else str(m)
            active = name_full == self._active_nano
            q_label, q_color, q_tip = model_quality(name_full)
            row = self.table_nano.rowCount()
            self.table_nano.insertRow(row)
            self.table_nano.setItem(row, 0, self._text_item(_basename(name_full)))
            s_item = self._text_item("● 当前激活" if active else "")
            if active:
                s_item.setForeground(_qcolor("#22c55e"))
            self.table_nano.setItem(row, 1, s_item)
            q_item = self._text_item(q_label)
            q_item.setToolTip(q_tip)
            q_item.setForeground(_qcolor(q_color))
            q_item.setTextAlignment(Qt.AlignCenter)
            self.table_nano.setItem(row, 2, q_item)
            desc = _shorten_path(name_full, 45)
            self.table_nano.setItem(row, 3, self._text_item(desc))
            # 绑定完整名称
            for c in range(4):
                it = self.table_nano.item(row, c)
                it.setData(Qt.UserRole, name_full)
        self.lbl_nano.setText(
            f"共 {len(self._models)} 个模型，激活: {_basename(self._active_nano) or '无'}")

        # 同时刷新选择表（Nano 部分）
        self._refresh_select_table()

    def _on_nano_select(self):
        self.btn_load.setEnabled(bool(self.table_nano.selectedItems()))

    def _on_load_selected(self):
        row = self.table_nano.currentRow()
        if row < 0:
            return
        name_full = self.table_nano.item(row, 0).data(Qt.UserRole) or ""
        if not name_full:
            return
        self.tcp.load_model(name_full)
        self.lbl_nano.setText(f"请求切换: {_basename(name_full)}")

    def _on_load_received(self, payload: dict):
        ok = payload.get("ok", False)
        msg = payload.get("message", payload.get("msg", ""))
        name = payload.get("model", "")
        if ok:
            self._active_nano = name
            self.lbl_status.setText(f"已切换下位机模型：{_basename(name)}")
            self.lbl_status.setStyleSheet(
                "color:#22c55e; font-size:17px; background:transparent;")
            self.lbl_nano.setText(f"切换成功: {_basename(name)}")
            self.refresh_nano_list()
            _info_popup(self, "切换成功",
                        f"下位机模型已切换为：{_basename(name)}\n\n"
                        "后续实时检测将使用该模型推理。")
        else:
            self.lbl_status.setText(f"切换失败: {msg or _basename(name)}")
            self.lbl_status.setStyleSheet(
                "color:#ef4444; font-size:17px; background:transparent;")
            self.lbl_nano.setText(f"切换失败: {msg or _basename(name)}")
            QMessageBox.warning(self, "模型切换", f"切换失败: {msg or _basename(name)}")

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
            in_lib = find_library_item(path) is not None
            self._add_local_item(path, in_library=in_lib)
            self._update_add_button_state()
            _info_popup(self, "已选择模型",
                        f"已选择模型文件：\n{os.path.basename(path)}\n\n"
                        "可点击「☆ 添加到模型库」保存，或「加载为本地推理模型」直接使用。")

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
        if models:
            _info_popup(self, "自动识别完成",
                        f"在常见目录中自动识别到 {len(models)} 个模型，"
                        "已加入下方列表。\n\n"
                        "如需保存常用模型，选中后点击「☆ 添加到模型库」。")
        else:
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
            if models:
                _info_popup(self, "扫描完成",
                            f"目录扫描完成，找到 {len(models)} 个模型，"
                            "已加入下方列表。\n\n"
                            "如需保存常用模型，选中后点击「☆ 添加到模型库」。")
            else:
                QMessageBox.information(
                    self, "扫描目录",
                    "该目录下未找到 .onnx / .pt 模型文件。")
        return len(models)

    def _add_local_item(self, path: str, in_library: bool = False):
        """将单个文件加入本地模型列表并选中；in_library=True 表示来自模型库"""
        lib_item = find_library_item(path)
        in_library = lib_item is not None or in_library
        for i in range(self.list_local.count()):
            it = self.list_local.item(i)
            if it.data(Qt.UserRole) == path:
                if in_library and not it.data(Qt.UserRole + 1):
                    text = self._local_item_text(path, in_library=True)
                    it.setText(text)
                    it.setData(Qt.UserRole + 1, True)
                self.list_local.setCurrentRow(i)
                self._update_add_button_state()
                return
        item = QListWidgetItem(self._local_item_text(path, in_library))
        item.setData(Qt.UserRole, path)
        item.setData(Qt.UserRole + 1, bool(in_library))
        item.setToolTip(path)
        tags = model_tags(path)
        item.setForeground(_qcolor(tags["color"] if tags["score"] or in_library else "#94a3b8"))
        self.list_local.addItem(item)
        self.list_local.setCurrentRow(self.list_local.count() - 1)
        self._update_add_button_state()

    def _local_item_text(self, path: str, in_library: bool) -> str:
        tags = model_tags(path)
        lib_item = find_library_item(path)
        display_name = lib_item.get("display_name", "") if lib_item else ""
        name = display_name or os.path.basename(path)
        q = f"{tags['quality_label']}({tags['score']})" if tags["score"] else tags["quality_label"]
        dataset = tags["dataset"] or "未知数据集"
        classes = f"{tags['classes']}类" if tags["classes"] else "类别未知"
        prefix = "[已入库]" if in_library else "[新]"
        rec = " ★" if tags["is_recommended"] else ""
        return f"{prefix} [{q}] {dataset}/{classes}{rec} {name}"

    def _on_local_item_clicked(self, item: QListWidgetItem):
        path = item.data(Qt.UserRole)
        if path:
            self.edit_local.setText(path)
            self._update_add_button_state()

    def _update_add_button_state(self):
        """已入库模型禁用「添加到模型库」，并改变按钮文案"""
        item = self.list_local.currentItem()
        if not item:
            self.btn_add_lib.setEnabled(True)
            self.btn_add_lib.setText("\u2606  添加到模型库")
            return
        path = item.data(Qt.UserRole) or ""
        in_lib = bool(item.data(Qt.UserRole + 1)) or find_library_item(path) is not None
        if in_lib:
            self.btn_add_lib.setEnabled(False)
            self.btn_add_lib.setText("\u2713  已在模型库")
        else:
            self.btn_add_lib.setEnabled(True)
            self.btn_add_lib.setText("\u2606  添加到模型库")

    def _restore_local_model(self):
        """打开对话框时：先列出模型库中所有模型，再恢复上次选中的本地模型"""
        self.list_local.clear()
        for e in load_library():
            p = e.get("path", "")
            if p and os.path.isfile(p) and p.lower().endswith(_LOCAL_EXTS):
                self._add_local_item(p, in_library=True)
        if self._default_model and os.path.isfile(self._default_model):
            self.edit_local.setText(self._default_model)
            self._add_local_item(self._default_model)
            self.lbl_local.setText("已恢复上次选中的本地模型")
        elif self.list_local.count():
            self.lbl_local.setText(f"模型库共 {self.list_local.count()} 个模型")
        self._update_add_button_state()

    # ---------- 模型库：添加 / 删除 ----------
    def _on_add_to_library(self):
        """把当前选中的模型持久化到模型库（去重），并自动记录数据集/类别"""
        path = self.edit_local.text().strip()
        if not path:
            item = self.list_local.currentItem()
            if item:
                path = item.data(Qt.UserRole) or ""
        if not path or not os.path.isfile(path):
            QMessageBox.information(self, "模型管理", "请先选择要添加的模型文件")
            return
        if not path.lower().endswith(_LOCAL_EXTS):
            QMessageBox.warning(self, "模型管理", "仅支持 .onnx / .pt 模型文件")
            return
        items = load_library()
        for e in items:
            if e.get("path", "") == path:
                self._add_local_item(path, in_library=True)
                self._update_add_button_state()
                QMessageBox.information(self, "模型管理", "该模型已在模型库中")
                return
        tags = model_tags(path)
        items.append({
            "path": path,
            "name": os.path.basename(path),
            "display_name": "",
            "size_mb": round(os.path.getsize(path) / 1e6, 2),
            "dataset": tags["dataset"],
            "classes": tags["classes"],
            "note": tags["tip"],
        })
        try:
            save_library(items)
        except Exception as e:
            QMessageBox.warning(self, "模型管理", f"写入模型库失败: {e}")
            return
        self._add_local_item(path, in_library=True)
        self._update_add_button_state()
        self.lbl_local.setText(f"已添加到模型库: {os.path.basename(path)}")
        self._refresh_select_table()
        ds = tags["dataset"] or "未知"
        cls = tags["classes"] or "未知"
        _info_popup(self, "添加成功",
                    f"模型已成功添加到模型库：\n{os.path.basename(path)}\n\n"
                    f"数据集：{ds}\n"
                    f"类别数：{cls}\n\n"
                    "下次打开模型管理仍会保留，可直接选用。")

    def _on_edit_library_info(self):
        """编辑模型库条目的显示名称和备注"""
        item = self.list_local.currentItem()
        if not item:
            QMessageBox.information(self, "编辑信息", "请先选中模型库中的模型")
            return
        path = item.data(Qt.UserRole) or ""
        lib_item = find_library_item(path)
        if not lib_item:
            QMessageBox.information(self, "编辑信息", "只有已入库的模型才能编辑信息")
            return

        # 弹出自定义输入框
        from PyQt5.QtWidgets import QInputDialog
        old_name = lib_item.get("display_name", "") or lib_item.get("name", os.path.basename(path))
        name, ok1 = QInputDialog.getText(
            self, "编辑显示名称", "显示名称（留空使用文件名）：",
            QLineEdit.Normal, old_name)
        if not ok1:
            return
        old_note = lib_item.get("note", "")
        note, ok2 = QInputDialog.getText(
            self, "编辑备注", "备注说明（如适用产线、缺陷类型等）：",
            QLineEdit.Normal, old_note)
        if not ok2:
            return

        items = load_library()
        for e in items:
            if e.get("path", "") == path:
                e["display_name"] = name.strip()
                e["note"] = note.strip()
                break
        try:
            save_library(items)
        except Exception as e:
            QMessageBox.warning(self, "编辑信息", f"保存失败: {e}")
            return
        # 刷新显示
        item.setText(self._local_item_text(path, in_library=True))
        self._refresh_select_table()
        _info_popup(self, "编辑成功",
                    f"模型信息已更新：\n{name.strip() or os.path.basename(path)}")

    def _on_delete_local(self):
        """删除选中的本地模型：从列表移除；若在模型库中则移除条目；
        文件删除需用户二次确认（默认仅移除条目，不删文件）"""
        item = self.list_local.currentItem()
        if not item:
            QMessageBox.information(self, "模型管理", "请先选中要删除的模型")
            return
        path = item.data(Qt.UserRole) or ""
        if not path:
            return
        name = os.path.basename(path)
        in_lib = bool(item.data(Qt.UserRole + 1))

        # 1) 从模型库移除条目（若存在）
        items = load_library()
        remain = [e for e in items if e.get("path", "") != path]
        if len(remain) != len(items):
            try:
                save_library(remain)
            except Exception as e:
                QMessageBox.warning(self, "模型管理", f"模型库写入失败: {e}")
                return

        # 2) 若文件存在，询问是否同时删除磁盘文件
        if os.path.isfile(path):
            box = QMessageBox(self)
            box.setWindowTitle("删除模型")
            box.setIcon(QMessageBox.Warning)
            box.setText(f"移除模型: {name}")
            box.setInformativeText(
                f"{path}\n\n是否同时删除磁盘上的文件？\n"
                "（选「仅从库移除」则文件保留，可再次添加）")
            btn_del = box.addButton("同时删除文件", QMessageBox.DestructiveRole)
            btn_keep = box.addButton("仅从库移除", QMessageBox.AcceptRole)
            box.setDefaultButton(btn_keep)
            box.exec_()
            if box.clickedButton() == btn_del:
                try:
                    os.remove(path)
                    info = "已删除文件，并从模型库/列表中移除"
                except OSError as e:
                    info = f"文件删除失败（可能被占用），已从模型库/列表移除。\n{e}"
            else:
                info = "已从模型库/列表移除，文件保留"
        else:
            info = "已从模型库/列表移除（文件本身已不存在）"

        # 3) 从列表移除
        row = self.list_local.row(item)
        self.list_local.takeItem(row)
        if self.edit_local.text() == path:
            self.edit_local.clear()
        self.lbl_local.setText(f"已移除: {name}")
        QMessageBox.information(self, "删除模型", info)

        # 4) 通知主程序并刷新选择表
        self.local_model_deleted.emit(path)
        self._refresh_select_table()
        self._update_add_button_state()

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

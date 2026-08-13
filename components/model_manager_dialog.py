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
    QFrame, QWidget, QApplication, QTabWidget, QProgressBar,
    QTreeWidget, QTreeWidgetItem, QTreeWidgetItemIterator
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
        open_tab 仍保留参数兼容(旧调用点),但新版已改为单页三区,
        不再切换 Tab,仅忽略该参数。
        """
        super().__init__(parent)
        self.tcp = tcp
        self._default_model = default_model
        self._default_model_dir = default_model_dir or ""
        self.setWindowTitle("模型管理")
        self.setMinimumSize(1200, 780)
        self.resize(1320, 820)
        self._models = []          # 下位机原始模型数据
        self._active_nano = ""     # 当前激活的下位机模型
        self._selected_path = ""   # 当前选中的路径或 nano 全名
        self._selected_source = "" # "nano" | "local" | "scan"
        self._scan_results = []    # 自动识别/扫描到的临时模型路径
        self._build_ui()
        self._connect_signals()
        self._update_source_status()
        self._refresh_all()

    # ---------- UI ----------
    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 14, 16, 14)
        root.setSpacing(12)

        # 顶部操作栏: 状态 + 全局操作
        toolbar = self._build_toolbar()
        root.addLayout(toolbar)

        # 主体: 左列表 / 右详情
        splitter = QSplitter(Qt.Horizontal)
        splitter.setHandleWidth(8)
        splitter.setStyleSheet("QSplitter::handle { background:#1f2937; }")

        self.left_panel = self._build_left_panel()
        splitter.addWidget(self.left_panel)

        self.right_panel = self._build_right_panel()
        splitter.addWidget(self.right_panel)

        splitter.setSizes([520, 720])
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

    def _build_toolbar(self) -> QHBoxLayout:
        lay = QHBoxLayout()
        lay.setSpacing(12)

        self.lbl_status = QLabel("推理源：检测中...")
        self.lbl_status.setStyleSheet(
            "color:#94a3b8; font-size:17px; background:transparent;")
        lay.addWidget(self.lbl_status)

        lay.addStretch()

        for txt, tip, slot, color in (
                ("\u21bb  重新连接", "重新连接下位机", self._on_reconnect, "blue"),
                ("\u21bb  刷新清单", "刷新下位机模型清单", self.refresh_nano_list, "gray"),
                ("\ud83d\udd0d  扫描目录", "扫描指定目录的 .onnx/.pt 模型", self._on_scan_dir, "gray"),
                ("\u2728  自动识别", "自动识别常见目录中的模型", self._on_auto_scan, "blue"),
                ("\u2606  添加模型", "浏览并添加本地模型", self._on_browse, "green")):
            b = QPushButton(txt)
            b.setFixedHeight(38)
            b.setCursor(Qt.PointingHandCursor)
            b.setToolTip(tip)
            if color == "blue":
                b.setStyleSheet(
                    "QPushButton { background:#1d4ed8; color:#ffffff; border:none; "
                    "border-radius:5px; padding:0 14px; font-size:15px; font-weight:500; }"
                    "QPushButton:hover { background:#2563eb; }"
                    "QPushButton:pressed { background:#1e40af; }")
            elif color == "green":
                b.setStyleSheet(
                    "QPushButton { background:#15803d; color:#ffffff; border:none; "
                    "border-radius:5px; padding:0 14px; font-size:15px; font-weight:500; }"
                    "QPushButton:hover { background:#16a34a; }"
                    "QPushButton:pressed { background:#14532d; }")
            else:
                b.setStyleSheet(
                    "QPushButton { background:#0f172a; color:#e2e8f0; border:1px solid #334155; "
                    "border-radius:5px; padding:0 14px; font-size:15px; font-weight:500; }"
                    "QPushButton:hover { background:#1e293b; border-color:#475569; }"
                    "QPushButton:pressed { background:#0b1120; }")
            b.clicked.connect(slot)
            lay.addWidget(b)
        return lay

    def _build_left_panel(self) -> QWidget:
        panel = QFrame()
        panel.setStyleSheet("QFrame { border:1px solid #1f2937; border-radius:6px; background:#0f172a; }")
        lay = QVBoxLayout(panel)
        lay.setContentsMargins(12, 12, 12, 12)
        lay.setSpacing(10)

        title = QLabel("模型列表")
        title.setStyleSheet(
            "border-left:4px solid #2563eb; padding-left:10px;"
            "font-size:18px; font-weight:600; color:#f1f5f9; background:transparent;")
        lay.addWidget(title)

        self.edit_search = QLineEdit()
        self.edit_search.setPlaceholderText("搜索模型名称 / 数据集 / 备注...")
        self.edit_search.setFixedHeight(36)
        self.edit_search.setStyleSheet(
            "QLineEdit { background:#0b1120; color:#e2e8f0; border:1px solid #334155; "
            "border-radius:5px; padding:0 10px; font-size:14px; }")
        self.edit_search.textChanged.connect(self._filter_tree)
        lay.addWidget(self.edit_search)

        self.tree = QTreeWidget()
        self.tree.setHeaderHidden(True)
        self.tree.setColumnCount(1)
        self.tree.setStyleSheet(
            "QTreeWidget { font-size:15px; background:#0b1120; border:none; border-radius:5px; padding:4px; }"
            "QTreeWidget::item { padding:6px 4px; border-bottom:1px solid #1f2937; }"
            "QTreeWidget::item:selected { background:#1e293b; color:#f1f5f9; }"
            "QTreeWidget::item:hover { background:#1e293b; }")
        self.tree.setIndentation(18)
        self.tree.itemSelectionChanged.connect(self._on_tree_select)
        lay.addWidget(self.tree, 1)

        self.lbl_list_hint = QLabel("提示：选中模型后在右侧查看详情并操作")
        self.lbl_list_hint.setStyleSheet("color:#64748b; font-size:14px; background:transparent;")
        lay.addWidget(self.lbl_list_hint)
        return panel

    def _build_right_panel(self) -> QWidget:
        panel = QFrame()
        panel.setStyleSheet("QFrame { border:1px solid #1f2937; border-radius:6px; background:#0f172a; }")
        lay = QVBoxLayout(panel)
        lay.setContentsMargins(14, 14, 14, 14)
        lay.setSpacing(12)

        title = QLabel("模型详情")
        title.setStyleSheet(
            "border-left:4px solid #22c55e; padding-left:10px;"
            "font-size:18px; font-weight:600; color:#f1f5f9; background:transparent;")
        lay.addWidget(title)

        # 名称
        self.detail_name = QLabel("未选择模型")
        self.detail_name.setStyleSheet(
            "font-size:20px; font-weight:600; color:#f1f5f9; background:transparent;")
        self.detail_name.setWordWrap(True)
        lay.addWidget(self.detail_name)

        # 来源 + 质量 一行
        meta = QHBoxLayout()
        meta.setSpacing(12)
        self.detail_source = QLabel("来源：--")
        self.detail_source.setStyleSheet("font-size:15px; color:#94a3b8; background:transparent;")
        meta.addWidget(self.detail_source)
        self.detail_quality = QLabel("质量：--")
        self.detail_quality.setStyleSheet("font-size:15px; font-weight:500; background:transparent;")
        meta.addWidget(self.detail_quality)
        meta.addStretch()
        lay.addLayout(meta)

        # 推荐/预训练提示
        self.detail_recommend = QLabel("")
        self.detail_recommend.setStyleSheet(
            "font-size:15px; font-weight:500; color:#22c55e; background:transparent;")
        lay.addWidget(self.detail_recommend)

        # 得分条
        self.detail_score = QProgressBar()
        self.detail_score.setRange(0, 100)
        self.detail_score.setValue(0)
        self.detail_score.setTextVisible(True)
        self.detail_score.setFormat("综合得分 %p")
        self.detail_score.setStyleSheet(
            "QProgressBar { border:1px solid #334155; border-radius:4px; text-align:center; "
            "color:#f1f5f9; font-size:14px; background:#0b1120; height:22px; }"
            "QProgressBar::chunk { border-radius:4px; background:#64748b; }")
        lay.addWidget(self.detail_score)

        # 数据集 / 类别 / 大小 三列
        spec = QHBoxLayout()
        spec.setSpacing(16)
        self.detail_dataset = QLabel("数据集：--")
        self.detail_dataset.setStyleSheet("font-size:15px; color:#94a3b8; background:transparent;")
        self.detail_classes = QLabel("类别：--")
        self.detail_classes.setStyleSheet("font-size:15px; color:#94a3b8; background:transparent;")
        self.detail_size = QLabel("大小：--")
        self.detail_size.setStyleSheet("font-size:15px; color:#94a3b8; background:transparent;")
        spec.addWidget(self.detail_dataset)
        spec.addWidget(self.detail_classes)
        spec.addWidget(self.detail_size)
        spec.addStretch()
        lay.addLayout(spec)

        # 说明
        self.detail_desc = QLabel("说明：--")
        self.detail_desc.setStyleSheet("font-size:15px; color:#94a3b8; background:transparent;")
        self.detail_desc.setWordWrap(True)
        lay.addWidget(self.detail_desc)

        # 路径(小字,作为 tooltip 即可,这里保留小字)
        self.detail_path = QLabel("")
        self.detail_path.setStyleSheet("font-size:13px; color:#64748b; background:transparent;")
        self.detail_path.setWordWrap(True)
        lay.addWidget(self.detail_path)

        lay.addStretch()

        # 本次扫描结果(右下角)
        scan_box = QFrame()
        scan_box.setStyleSheet(
            "QFrame { border:1px solid #1f2937; border-radius:6px; background:#0b1120; }")
        scan_lay = QVBoxLayout(scan_box)
        scan_lay.setContentsMargins(10, 10, 10, 10)
        scan_lay.setSpacing(8)

        scan_title = QLabel("本次扫描结果")
        scan_title.setStyleSheet(
            "border-left:4px solid #eab308; padding-left:8px;"
            "font-size:16px; font-weight:600; color:#f1f5f9; background:transparent;")
        scan_lay.addWidget(scan_title)

        self.lbl_scan_hint = QLabel("点击「自动识别」或「扫描目录」后,未入库模型将显示在这里")
        self.lbl_scan_hint.setStyleSheet("font-size:13px; color:#64748b; background:transparent;")
        self.lbl_scan_hint.setWordWrap(True)
        scan_lay.addWidget(self.lbl_scan_hint)

        self.scan_list = QListWidget()
        self.scan_list.setFixedHeight(180)
        self.scan_list.setStyleSheet(
            "QListWidget { font-size:14px; background:#0f172a; border:none; border-radius:5px; padding:4px; }"
            "QListWidget::item { padding:5px 4px; border-bottom:1px solid #1f2937; }"
            "QListWidget::item:selected { background:#1e293b; color:#f1f5f9; }"
            "QListWidget::item:hover { background:#1e293b; }")
        self.scan_list.itemClicked.connect(self._on_scan_item_clicked)
        scan_lay.addWidget(self.scan_list)
        lay.addWidget(scan_box)

        # 操作按钮区
        self.action_box = QWidget()
        action_layout = QVBoxLayout(self.action_box)
        action_layout.setContentsMargins(0, 0, 0, 0)
        action_layout.setSpacing(10)

        self.btn_load = QPushButton("\u25b6  加载为当前模型")
        self.btn_load.setFixedHeight(44)
        self.btn_load.setCursor(Qt.PointingHandCursor)
        self.btn_load.setEnabled(False)
        self.btn_load.setStyleSheet(
            "QPushButton { background:#16a34a; color:#ffffff; border:none; "
            "border-radius:5px; padding:0 20px; font-size:16px; font-weight:600; }"
            "QPushButton:hover { background:#22c55e; }"
            "QPushButton:pressed { background:#15803d; }"
            "QPushButton:disabled { background:#334155; color:#94a3b8; }")
        self.btn_load.clicked.connect(self._on_load_current)
        action_layout.addWidget(self.btn_load)

        sec = QHBoxLayout()
        sec.setSpacing(10)

        self.btn_add_lib = QPushButton("\u2606  添加到模型库")
        self.btn_add_lib.setFixedHeight(38)
        self.btn_add_lib.setCursor(Qt.PointingHandCursor)
        self.btn_add_lib.setEnabled(False)
        self.btn_add_lib.setStyleSheet(
            "QPushButton { background:#0f172a; color:#e2e8f0; border:1px solid #334155; "
            "border-radius:5px; padding:0 14px; font-size:15px; font-weight:500; }"
            "QPushButton:hover { background:#1e293b; border-color:#475569; }")
        self.btn_add_lib.clicked.connect(self._on_add_to_library)

        self.btn_edit_info = QPushButton("\u270e  编辑信息")
        self.btn_edit_info.setFixedHeight(38)
        self.btn_edit_info.setCursor(Qt.PointingHandCursor)
        self.btn_edit_info.setEnabled(False)
        self.btn_edit_info.setStyleSheet(
            "QPushButton { background:#0f172a; color:#e2e8f0; border:1px solid #334155; "
            "border-radius:5px; padding:0 14px; font-size:15px; font-weight:500; }"
            "QPushButton:hover { background:#1e293b; border-color:#475569; }")
        self.btn_edit_info.clicked.connect(self._on_edit_library_info)

        self.btn_del = QPushButton("\ud83d\uddd1  删除")
        self.btn_del.setFixedHeight(38)
        self.btn_del.setCursor(Qt.PointingHandCursor)
        self.btn_del.setEnabled(False)
        self.btn_del.setStyleSheet(
            "QPushButton { background:#0f172a; color:#ef4444; border:1px solid #7f1d1d; "
            "border-radius:5px; padding:0 14px; font-size:15px; font-weight:500; }"
            "QPushButton:hover { background:#1e293b; border-color:#dc2626; }")
        self.btn_del.clicked.connect(self._on_delete_local)
        sec.addWidget(self.btn_add_lib)
        sec.addWidget(self.btn_edit_info)
        sec.addWidget(self.btn_del)
        action_layout.addLayout(sec)
        lay.addWidget(self.action_box)
        return panel

    # ---------- Tab 1: 选择模型 ----------
    # ---------- Tab 2: 模型管理 ----------
    def _connect_signals(self):
        self.tcp.model_list_received.connect(self._on_list_received)
        self.tcp.model_load_received.connect(self._on_load_received)

    # ---------- 公共状态 ----------
    def _update_source_status(self):
        if self.tcp.is_connected:
            self.lbl_status.setText("推理源：下位机已连接，优先使用下位机推理")
            self.lbl_status.setStyleSheet(
                "color:#22c55e; font-size:17px; background:transparent;")
        else:
            host = getattr(self.tcp, "host", "192.168.1.101")
            port = getattr(self.tcp, "port", 8888)
            self.lbl_status.setText(
                f"推理源：下位机未连接（{host}:{port}），可选用本地模型")
            self.lbl_status.setStyleSheet(
                "color:#94a3b8; font-size:17px; background:transparent;")

    def _refresh_all(self):
        self._restore_local_model()
        self.refresh_nano_list()
        self._refresh_tree()
        self._refresh_scan_panel()

    # ---------- 树形模型列表 ----------
    def _refresh_tree(self):
        """汇总 Nano 模型 + 本地模型库,刷新左侧树形列表"""
        self.tree.clear()
        search = self.edit_search.text().strip().lower() if hasattr(self, "edit_search") else ""

        # 本地模型库分组
        local_root = QTreeWidgetItem(self.tree, ["本地库"])
        local_root.setData(0, Qt.UserRole, "__group_local__")
        local_root.setFlags(local_root.flags() | Qt.ItemIsEnabled)
        local_items = []
        for e in load_library():
            p = e.get("path", "")
            if p and os.path.isfile(p):
                local_items.append({"path": p, "source": "local", "in_lib": True})
        # 默认本地模型（若不在库中）
        if self._default_model and os.path.isfile(self._default_model):
            if not any(e["path"] == self._default_model for e in local_items):
                local_items.append({"path": self._default_model, "source": "local", "in_lib": False})
        for item in local_items:
            self._add_tree_item(local_root, item)

        # 下位机模型分组
        nano_root = QTreeWidgetItem(self.tree, ["下位机"])
        nano_root.setData(0, Qt.UserRole, "__group_nano__")
        nano_root.setFlags(nano_root.flags() | Qt.ItemIsEnabled)
        for m in self._models:
            name = m.get("name", "") if isinstance(m, dict) else str(m)
            self._add_tree_item(nano_root, {"name": name, "source": "nano", "data": m})

        self.tree.expandAll()

        # 搜索过滤
        if search:
            self._filter_tree(search)

        # 恢复默认选中
        if self._selected_path:
            self._select_tree_item(self._selected_path)
        elif self._default_model:
            self._select_tree_item(self._default_model)

        # 更新提示
        total = len(local_items) + len(self._models)
        self.lbl_list_hint.setText(
            f"共 {total} 个模型：本地 {len(local_items)}，下位机 {len(self._models)}。"
            "选中后在右侧操作。")

    def _refresh_scan_panel(self):
        """刷新右下角扫描结果列表"""
        self.scan_list.clear()
        default_norm = os.path.normpath(self._default_model or "")
        visible = [
            path for path in self._scan_results
            if os.path.isfile(path) and not self._path_in_library(path)
            and os.path.normpath(path) != default_norm
        ]
        if visible:
            self.lbl_scan_hint.setText(f"未入库 {len(visible)} 个,选中后在上方详情区操作")
            for path in visible:
                tags = model_tags(path)
                name = os.path.basename(path)
                dataset = tags["dataset"] or "未知"
                classes = f"{tags['classes']}类" if tags['classes'] else "未知"
                label = tags["quality_label"]
                score = tags["score"]
                prefix = "★ " if tags["is_recommended"] else ""
                text = f"{prefix}{name} · {dataset}/{classes} · {label}{f'({score})' if score else ''}"
                item = QListWidgetItem(text)
                item.setData(Qt.UserRole, path)
                item.setToolTip(path)
                item.setForeground(_qcolor(tags["color"]))
                self.scan_list.addItem(item)
        else:
            self.lbl_scan_hint.setText("点击「自动识别」或「扫描目录」后,未入库模型将显示在这里")

    def _on_scan_item_clicked(self, item: QListWidgetItem):
        """点击右下角扫描结果:选中并渲染详情"""
        path = item.data(Qt.UserRole)
        if not path:
            return
        self.tree.clearSelection()
        self._selected_path = path
        self._selected_source = "scan"
        self._render_detail(path, "scan")
        self.btn_load.setEnabled(True)

    def _add_tree_item(self, parent: QTreeWidgetItem, item: dict):
        """向树中添加一个模型条目"""
        if item.get("source") == "local":
            path = item["path"]
            tags = model_tags(path)
            lib_item = find_library_item(path)
            in_lib = lib_item is not None or item.get("in_lib", False)
            label = tags["quality_label"]
            score = tags["score"]
            color = tags["color"]
            display_name = lib_item.get("display_name", "") if lib_item else ""
            name = display_name or os.path.basename(path)
            dataset = tags["dataset"] or "未知"
            classes = f"{tags['classes']}类" if tags['classes'] else "未知"
            prefix = "★ " if tags["is_recommended"] else ""
            text = f"{prefix}{name} · {dataset}/{classes} · {label}{f'({score})' if score else ''}"
            tree_item = QTreeWidgetItem(parent, [text])
            tree_item.setForeground(0, _qcolor(color))
            tree_item.setToolTip(0, path)
            tree_item.setData(0, Qt.UserRole, path)
            tree_item.setData(0, Qt.UserRole + 1, "local")
            tree_item.setData(0, Qt.UserRole + 2, in_lib)
        elif item.get("source") == "scan":
            path = item["path"]
            tags = model_tags(path)
            name = os.path.basename(path)
            dataset = tags["dataset"] or "未知"
            classes = f"{tags['classes']}类" if tags['classes'] else "未知"
            label = tags["quality_label"]
            score = tags["score"]
            color = tags["color"]
            prefix = "★ " if tags["is_recommended"] else ""
            text = f"{prefix}[未入库] {name} · {dataset}/{classes} · {label}{f'({score})' if score else ''}"
            tree_item = QTreeWidgetItem(parent, [text])
            tree_item.setForeground(0, _qcolor(color))
            tree_item.setToolTip(0, path)
            tree_item.setData(0, Qt.UserRole, path)
            tree_item.setData(0, Qt.UserRole + 1, "scan")
        else:
            m = item.get("data", {})
            name_full = m.get("name", "") if isinstance(m, dict) else str(m)
            name = _basename(name_full)
            active = name_full == self._active_nano
            q_label, q_color, q_tip = model_quality(name_full)
            tags = model_tags(name_full)
            dataset = tags["dataset"] or "未知"
            classes = f"{tags['classes']}类" if tags['classes'] else "未知"
            prefix = "● " if active else ""
            text = f"{prefix}{name} · {dataset}/{classes} · {q_label}"
            tree_item = QTreeWidgetItem(parent, [text])
            tree_item.setForeground(0, _qcolor(q_color))
            tree_item.setToolTip(0, name_full)
            tree_item.setData(0, Qt.UserRole, name_full)
            tree_item.setData(0, Qt.UserRole + 1, "nano")
            if active:
                tree_item.setForeground(0, _qcolor("#22c55e"))

    def _select_tree_item(self, key: str):
        """根据路径或名称选中树项"""
        it = QTreeWidgetItemIterator(self.tree)
        while it.value():
            item = it.value()
            data = item.data(0, Qt.UserRole) or ""
            if data == key:
                self.tree.setCurrentItem(item)
                return
            it += 1

    def _filter_tree(self, text: str = ""):
        """根据搜索文本过滤树项"""
        if not hasattr(self, "tree"):
            return
        if text == "":
            text = self.edit_search.text().strip().lower()
        root = self.tree.invisibleRootItem()
        for i in range(root.childCount()):
            group = root.child(i)
            group.setHidden(False)
            visible_children = 0
            for j in range(group.childCount()):
                child = group.child(j)
                item_text = child.text(0).lower()
                tooltip = (child.toolTip(0) or "").lower()
                show = not text or text in item_text or text in tooltip
                child.setHidden(not show)
                if show:
                    visible_children += 1
            group.setHidden(visible_children == 0 and text != "")

    def _on_tree_select(self):
        item = self.tree.currentItem()
        if not item:
            self._clear_detail()
            return
        key = item.data(0, Qt.UserRole) or ""
        source = item.data(0, Qt.UserRole + 1) or ""
        if source not in ("local", "nano", "scan"):
            # 点击的是分组节点,不处理
            self._clear_detail()
            return
        # 切换为左侧模型时,清空右下角扫描列表的选中态,避免视觉混淆
        self.scan_list.clearSelection()
        self._selected_path = key
        self._selected_source = source
        self._render_detail(key, source)
        self.btn_load.setEnabled(True)

    def _clear_detail(self):
        self.detail_name.setText("未选择模型")
        self.detail_source.setText("来源：--")
        self.detail_quality.setText("质量：--")
        self.detail_quality.setStyleSheet("font-size:15px; font-weight:500; color:#94a3b8; background:transparent;")
        self.detail_recommend.setText("")
        self.detail_score.setValue(0)
        self._set_score_color(0)
        self.detail_dataset.setText("数据集：--")
        self.detail_classes.setText("类别：--")
        self.detail_size.setText("大小：--")
        self.detail_desc.setText("说明：--")
        self.detail_path.setText("")
        self.btn_load.setEnabled(False)
        self.btn_load.setText("\u25b6  加载为当前模型")
        self.btn_add_lib.setEnabled(False)
        self.btn_add_lib.setText("\u2606  添加到模型库")
        self.btn_edit_info.setEnabled(False)
        self.btn_del.setEnabled(False)

    def _render_detail(self, key: str, source: str):
        if source in ("local", "scan"):
            path = key
            lib_item = find_library_item(path)
            tags = model_tags(path)
            display_name = lib_item.get("display_name", "") if lib_item else ""
            name = display_name or os.path.basename(path)
            self.detail_name.setText(name)
            self.detail_source.setText("来源：本地 PC 模型")
            label, score, color, tip = score_quality(path)
            self.detail_quality.setText(f"质量：{label}")
            self.detail_quality.setStyleSheet(f"font-size:15px; font-weight:500; color:{color}; background:transparent;")
            self.detail_score.setValue(score if score else 0)
            self._set_score_color(score if score else 0)
            dataset = tags["dataset"] or "未知"
            classes = f"{tags['classes']}类" if tags['classes'] else "未知"
            self.detail_dataset.setText(f"数据集：{dataset}")
            self.detail_classes.setText(f"类别：{classes}")
            if tags["is_recommended"]:
                self.detail_recommend.setText("★ 推荐直接使用（高质量 + 已知数据集）")
                self.detail_recommend.setStyleSheet("font-size:15px; font-weight:500; color:#22c55e; background:transparent;")
            elif tags["is_pretrained"]:
                self.detail_recommend.setText("⚠ 预训练 backbone，不适合直接用于缺陷检测")
                self.detail_recommend.setStyleSheet("font-size:15px; font-weight:500; color:#ef4444; background:transparent;")
            else:
                self.detail_recommend.setText("")
            try:
                size_mb = os.path.getsize(path) / 1e6
                self.detail_size.setText(f"大小：{size_mb:.2f} MB")
            except Exception:
                self.detail_size.setText("大小：--")
            note = lib_item.get("note", "") if lib_item else tip
            self.detail_desc.setText(f"说明：{note}")
            self.detail_path.setText(f"路径：{_shorten_path(path, 80)}")
            self.detail_path.setToolTip(path)
            self.btn_load.setText("\u25b6  加载为本地推理模型")
            in_lib = lib_item is not None
            self.btn_add_lib.setEnabled(not in_lib)
            self.btn_add_lib.setText("\u2713  已在模型库" if in_lib else "\u2606  添加到模型库")
            self.btn_edit_info.setEnabled(in_lib)
            self.btn_del.setEnabled(True)
        else:
            name_full = key
            name = _basename(name_full)
            tags = model_tags(name_full)
            self.detail_name.setText(name)
            self.detail_source.setText("来源：下位机（Nano）")
            label, color, tip = model_quality(name_full)
            self.detail_quality.setText(f"质量：{label}")
            self.detail_quality.setStyleSheet(f"font-size:15px; font-weight:500; color:{color}; background:transparent;")
            self.detail_score.setValue(0)
            self._set_score_color(0)
            dataset = tags["dataset"] or "未知"
            classes = f"{tags['classes']}类" if tags['classes'] else "未知"
            self.detail_dataset.setText(f"数据集：{dataset}")
            self.detail_classes.setText(f"类别：{classes}")
            self.detail_size.setText("大小：--")
            if tags["is_recommended"]:
                self.detail_recommend.setText("★ 推荐直接使用")
                self.detail_recommend.setStyleSheet("font-size:15px; font-weight:500; color:#22c55e; background:transparent;")
            else:
                self.detail_recommend.setText("")
            self.detail_desc.setText(f"说明：{tip}")
            self.detail_path.setText(f"路径：{_shorten_path(name_full, 80)}")
            self.detail_path.setToolTip(name_full)
            self.btn_load.setText("\u25b6  切换为当前下位机模型")
            self.btn_add_lib.setEnabled(False)
            self.btn_add_lib.setText("\u2606  添加到模型库")
            self.btn_edit_info.setEnabled(False)
            self.btn_del.setEnabled(False)

    def _on_load_current(self):
        """右侧加载按钮：根据当前选中来源执行本地加载或 Nano 切换"""
        if self._selected_source in ("local", "scan"):
            path = self._selected_path
            if not path or not os.path.isfile(path):
                QMessageBox.warning(self, "加载模型", "选中的本地模型文件不存在")
                return
            self.local_model_selected.emit(path)
            self.lbl_status.setText(f"已加载本地模型：{os.path.basename(path)}")
            self.lbl_status.setStyleSheet("color:#22c55e; font-size:17px; background:transparent;")
            self._default_model = path
            # 若来自扫描结果,加载后保留在扫描结果中(用户可再添加到库)
            self._refresh_tree()
        elif self._selected_source == "nano":
            name = self._selected_path
            self.tcp.load_model(name)
            self.lbl_status.setText(f"请求切换下位机模型: {_basename(name)}")
            self.lbl_status.setStyleSheet("color:#eab308; font-size:17px; background:transparent;")
        else:
            QMessageBox.information(self, "加载模型", "请先选择一个模型")

    # ---------- Tab 1: 选择模型 数据 ----------
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

    # ---------- Nano 清单 ----------
    def refresh_nano_list(self):
        self._update_source_status()
        if not self.tcp.is_connected:
            self.lbl_status.setText("推理源：下位机未连接，可选用本地模型")
            self.lbl_status.setStyleSheet("color:#94a3b8; font-size:17px; background:transparent;")
            return
        self.lbl_status.setText("正在获取下位机模型清单...")
        self.lbl_status.setStyleSheet("color:#eab308; font-size:17px; background:transparent;")
        self.tcp.request_model_list()

    def _on_list_received(self, payload: dict):
        self._models = payload.get("models", [])
        self._active_nano = payload.get("active", "")
        self._update_source_status()
        self._refresh_tree()
        active_name = _basename(self._active_nano) or "无"
        self.lbl_status.setText(
            f"下位机共 {len(self._models)} 个模型，当前激活: {active_name}")
        self.lbl_status.setStyleSheet(
            "color:#22c55e; font-size:17px; background:transparent;")

    def _on_load_received(self, payload: dict):
        ok = payload.get("ok", False)
        msg = payload.get("message", payload.get("msg", ""))
        name = payload.get("model", "")
        if ok:
            self._active_nano = name
            self.lbl_status.setText(f"已切换下位机模型：{_basename(name)}")
            self.lbl_status.setStyleSheet(
                "color:#22c55e; font-size:17px; background:transparent;")
            self.refresh_nano_list()
            _info_popup(self, "切换成功",
                        f"下位机模型已切换为：{_basename(name)}\n\n"
                        "后续实时检测将使用该模型推理。")
        else:
            self.lbl_status.setText(f"切换失败: {msg or _basename(name)}")
            self.lbl_status.setStyleSheet(
                "color:#ef4444; font-size:17px; background:transparent;")
            QMessageBox.warning(self, "模型切换", f"切换失败: {msg or _basename(name)}")

    def _on_reconnect(self):
        self.lbl_status.setText("正在重新连接下位机...")
        self.lbl_status.setStyleSheet("color:#eab308; font-size:17px; background:transparent;")
        self.reconnect_requested.emit()
    # ---------- 本地模型 ----------
    def _on_browse(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "选择本地模型", self._default_model_dir,
            "模型文件 (*.onnx *.pt)")
        if path:
            self._default_model = path
            self._default_model_dir = os.path.dirname(path)
            self._refresh_tree()
            self._select_tree_item(path)
            self.lbl_status.setText(f"已选择本地模型：{os.path.basename(path)}，可点击右侧「加载」或「添加到模型库」")
            self.lbl_status.setStyleSheet("color:#94a3b8; font-size:17px; background:transparent;")

    def _on_scan_dir(self):
        d = QFileDialog.getExistingDirectory(
            self, "扫描模型目录", self._default_model_dir)
        if d:
            self._scan_directory(d)

    def _on_auto_scan(self):
        """自动识别常见模型目录和上次模型目录中的 .onnx/.pt 文件"""
        self.lbl_status.setText("自动识别中...")
        self.lbl_status.setStyleSheet("color:#94a3b8; font-size:17px; background:transparent;")
        QApplication.processEvents()

        if _auto_scan is None:
            QMessageBox.warning(self, "自动识别", "自动扫描模块未加载，请检查 tools/auto_scan_models.py 是否存在。")
            self.lbl_status.setText("自动识别失败")
            return

        models = _auto_scan(max_depth=5)
        # 记忆扫描结果(去重,排除已入库),并在右下角扫描结果列表中展示
        seen = set()
        self._scan_results = []
        for m in models:
            path = m.get("path", "")
            if not path or not os.path.isfile(path):
                continue
            norm = os.path.normpath(path)
            if norm in seen or self._path_in_library(path) or norm == os.path.normpath(self._default_model or ""):
                continue
            seen.add(norm)
            self._scan_results.append(path)
        self._refresh_tree()
        self._refresh_scan_panel()
        self.lbl_status.setText(f"自动识别到 {len(models)} 个模型，未入库 {len(self._scan_results)} 个")
        if models:
            _info_popup(self, "自动识别完成",
                        f"在常见目录中自动识别到 {len(models)} 个模型。\n\n"
                        "选中模型后可在右侧查看详情并添加/加载。")
        else:
            QMessageBox.information(
                self, "自动识别",
                "在常见目录中未找到 .onnx / .pt 模型文件。\n\n"
                "请使用「扫描目录」手动选择模型所在文件夹。")

    def _scan_directory(self, root: str, add_to_list=True, clear_first=True) -> int:
        """扫描目录下的模型文件,默认递归深度 5"""
        self.lbl_status.setText("扫描中...")
        self.lbl_status.setStyleSheet("color:#94a3b8; font-size:17px; background:transparent;")
        QApplication.processEvents()

        if _scan_models is None:
            QMessageBox.warning(self, "扫描目录", "自动扫描模块未加载，请检查 tools/auto_scan_models.py 是否存在。")
            self.lbl_status.setText("扫描失败")
            return 0

        models = _scan_models([root], max_depth=5)
        seen = set()
        self._scan_results = []
        for m in models:
            path = m.get("path", "")
            if not path or not os.path.isfile(path):
                continue
            norm = os.path.normpath(path)
            if norm in seen or self._path_in_library(path) or norm == os.path.normpath(self._default_model or ""):
                continue
            seen.add(norm)
            self._scan_results.append(path)
        self._refresh_tree()
        self._refresh_scan_panel()
        self.lbl_status.setText(f"扫描到 {len(models)} 个模型，未入库 {len(self._scan_results)} 个")
        if models:
            _info_popup(self, "扫描完成",
                        f"目录扫描完成，找到 {len(models)} 个模型。\n\n"
                        "选中模型后可在右侧查看详情并添加/加载。")
        else:
            QMessageBox.information(
                self, "扫描目录",
                "该目录下未找到 .onnx / .pt 模型文件。")
        return len(models)

    def _restore_local_model(self):
        """打开对话框时恢复上次选中的本地模型路径"""
        if self._default_model and os.path.isfile(self._default_model):
            self._default_model_dir = os.path.dirname(self._default_model)

    def _path_in_library(self, path: str) -> bool:
        """判断路径是否已存在于模型库(按规范化路径比较,兼容 / 与 \\)"""
        norm = os.path.normpath(path)
        for e in load_library():
            if os.path.normpath(e.get("path", "")) == norm:
                return True
        return False

    # ---------- 模型库：添加 / 删除 ----------
    def _on_add_to_library(self):
        """把当前选中的本地模型持久化到模型库（去重），并自动记录数据集/类别"""
        path = self._selected_path
        if not path or not os.path.isfile(path):
            QMessageBox.information(self, "模型管理", "请先选择一个本地模型")
            return
        if not path.lower().endswith(_LOCAL_EXTS):
            QMessageBox.warning(self, "模型管理", "仅支持 .onnx / .pt 模型文件")
            return
        items = load_library()
        for e in items:
            if e.get("path", "") == path:
                self._refresh_tree()
                self._select_tree_item(path)
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
        # 从扫描结果中移除,避免重复显示
        if path in self._scan_results:
            self._scan_results.remove(path)
        self._refresh_tree()
        self._refresh_scan_panel()
        self._select_tree_item(path)
        self.lbl_status.setText(f"已添加到模型库: {os.path.basename(path)}")
        self.lbl_status.setStyleSheet("color:#22c55e; font-size:17px; background:transparent;")
        ds = tags["dataset"] or "未知"
        cls = tags["classes"] or "未知"
        _info_popup(self, "添加成功",
                    f"模型已成功添加到模型库：\n{os.path.basename(path)}\n\n"
                    f"数据集：{ds}\n"
                    f"类别数：{cls}\n\n"
                    "下次打开模型管理仍会保留，可直接选用。")

    def _on_edit_library_info(self):
        """编辑模型库条目的显示名称和备注"""
        path = self._selected_path
        if not path:
            QMessageBox.information(self, "编辑信息", "请先选中模型库中的模型")
            return
        lib_item = find_library_item(path)
        if not lib_item:
            QMessageBox.information(self, "编辑信息", "只有已入库的模型才能编辑信息")
            return

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
        self._refresh_tree()
        self._select_tree_item(path)
        _info_popup(self, "编辑成功",
                    f"模型信息已更新：\n{name.strip() or os.path.basename(path)}")

    def _on_delete_local(self):
        """删除选中的本地模型：从模型库移除；扫描结果未入库则仅从列表移除；文件删除需二次确认（默认保留文件）"""
        path = self._selected_path
        if not path:
            QMessageBox.information(self, "模型管理", "请先选中要删除的模型")
            return
        name = os.path.basename(path)
        lib_item = find_library_item(path)
        in_lib = lib_item is not None

        # 扫描结果且未入库：直接从扫描结果中移除,不删文件
        if self._selected_source == "scan" and not in_lib:
            if path in self._scan_results:
                self._scan_results.remove(path)
            self._clear_detail()
            self._refresh_tree()
            self.lbl_status.setText(f"已从扫描结果移除: {name}")
            self.lbl_status.setStyleSheet("color:#94a3b8; font-size:17px; background:transparent;")
            return

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

        self._clear_detail()
        self._refresh_tree()
        self._refresh_scan_panel()
        self.lbl_status.setText(f"已移除: {name}")
        self.lbl_status.setStyleSheet("color:#94a3b8; font-size:17px; background:transparent;")
        QMessageBox.information(self, "删除模型", info)

        # 通知主程序
        self.local_model_deleted.emit(path)

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

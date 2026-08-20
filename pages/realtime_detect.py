"""
P1 实时检测页（设计稿图 1）
左栏：相机设置 / 检测参数 / ROI 设置 / 开始停止 / 保存图像
中部：实时预览（工具栏 + 预览区：ROI 绿框、缺陷红框、NG 浮窗）
右栏：检测结果 KPI + 当前结果详情
底部：检测历史 | 通信设置+存储设置+模型设置 | 运行日志
"""
from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QComboBox,
    QLineEdit, QTabWidget, QTextEdit, QDialog, QProgressBar,
    QGridLayout, QSizePolicy, QFileDialog, QTableWidgetItem, QHeaderView,
    QScrollArea
)
from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtGui import QColor

from components.common_widgets import (
    Card, KPICard, StatusLight, StyledTable, form_row,
    SpinBox, DoubleSpinBox, FocusComboBox, SegGroup
)
from components.image_preview import ImagePreview
from components.roi_editor import RoiEditDialog

_LBL = "color:#cbd5e1; font-size:16px; background:transparent;"


def _lbl(text):
    l = QLabel(text)
    l.setStyleSheet(_LBL)
    return l


class RealtimeDetectPage(QWidget):
    start_requested = pyqtSignal()
    stop_requested = pyqtSignal()
    save_image_requested = pyqtSignal(str)
    local_image_requested = pyqtSignal()
    model_mgr_requested = pyqtSignal()
    load_model_requested = pyqtSignal(str)
    save_path_changed = pyqtSignal(str)
    reconnect_requested = pyqtSignal()
    roi_changed = pyqtSignal(list)
    conf_changed = pyqtSignal(float)   # 实时置信度数值变化

    def __init__(self, parent=None):
        super().__init__(parent)
        self._rois = []
        self._build()

    # ================= 布局 =================
    def _build(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(10, 10, 10, 10)
        root.setSpacing(10)

        top = QHBoxLayout()
        top.setSpacing(10)
        top.addLayout(self._build_left(), 3)
        top.addLayout(self._build_center(), 6)
        top.addLayout(self._build_right(), 4)
        root.addLayout(top, 9)

        bottom = QHBoxLayout()
        bottom.setSpacing(10)
        bottom.addLayout(self._build_history(), 5)
        bottom.addLayout(self._build_mid_bottom(), 4)
        bottom.addLayout(self._build_mini_log(), 4)
        root.addLayout(bottom, 1)

    def set_source(self, text: str, color: str = "#22d3ee"):
        """设置当前检测源提示（PC 本地 / Nano 下位机）"""
        if not hasattr(self, "lbl_source"):
            return
        self.lbl_source.setText(f"当前检测源: {text}")
        self.lbl_source.setStyleSheet(
            f"color:{color}; font-size:14px; padding:6px 10px;"
            "background:#0f172a; border:1px solid #1e293b; border-radius:6px;")

    # ---------- 检测模式 ----------
    def _build_mode_card(self):
        card = Card("检测模式")
        card.body.setSpacing(6)

        self.seg_mode = SegGroup(["单次检测", "多次检测"])
        self.seg_mode.selected.connect(self._on_mode_changed)
        card.body.addWidget(self.seg_mode)

        # 推理源
        self.combo_source = FocusComboBox()
        self.combo_source.addItems(["PC 本地模型", "Nano 下位机模型"])
        card.body.addLayout(form_row("推理源", self.combo_source, 70))

        # 模式提示
        self.lbl_mode_hint = QLabel("单次：一次选择一张图片")
        self.lbl_mode_hint.setWordWrap(True)
        self.lbl_mode_hint.setStyleSheet(
            "color:#94a3b8; font-size:13px; background:transparent;")
        card.body.addWidget(self.lbl_mode_hint)

        return card

    def _on_mode_changed(self, mode: str):
        """单次/多次切换：更新提示"""
        if mode == "多次检测":
            self.lbl_mode_hint.setText("多次：可选多张图片，点「开始检测」批量检测")
        else:
            self.lbl_mode_hint.setText("单次：一次选择一张图片")

    def get_detect_mode(self) -> str:
        return self.seg_mode.current()

    def get_infer_source(self) -> str:
        return self.combo_source.currentText()

    def set_infer_source(self, name: str):
        """设置推理源下拉（PC 本地模型 / Nano 下位机模型）"""
        idx = self.combo_source.findText(name)
        if idx >= 0:
            self.combo_source.setCurrentIndex(idx)

    def update_nav(self, index: int, total: int):
        """兼容占位：上一张/下一张功能已移除（8-20），保留签名供 main.py 调用"""
        pass

    # ---------- 左栏 ----------
    def _build_left(self):
        # 整体垂直布局
        left_layout = QVBoxLayout()
        left_layout.setSpacing(0)
        left_layout.setContentsMargins(0, 0, 0, 0)

        # 滚动区域：包含三个卡片
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)
        scroll.setMinimumHeight(400)  # 设置最小高度确保内容可见
        scroll.setStyleSheet("QScrollArea{background:transparent;} QWidget{background:transparent;}")
        
        col = QVBoxLayout()
        col.setSpacing(4)

        cam = Card("相机设置")
        cam.body.setSpacing(4)
        self.combo_camera = FocusComboBox()
        self.combo_camera.addItems(["相机 01", "相机 02"])
        self.spin_exposure = DoubleSpinBox()
        self.spin_exposure.setRange(0.1, 1000)
        self.spin_exposure.setValue(10.0)
        self.spin_gain = DoubleSpinBox()
        self.spin_gain.setRange(0, 48)
        self.spin_gain.setValue(2.0)
        self.spin_bright = SpinBox()
        self.spin_bright.setRange(0, 255)
        self.spin_bright.setValue(128)
        for lbl, w in (("相机选择", self.combo_camera), ("曝光时间 (ms)", self.spin_exposure),
                       ("增益 (dB)", self.spin_gain), ("光源亮度", self.spin_bright)):
            cam.body.addLayout(form_row(lbl, w, 160))
        col.addWidget(cam)

        col.addWidget(self._build_mode_card())

        det = Card("检测参数")
        det.body.setSpacing(4)
        self.spin_conf = DoubleSpinBox()
        self.spin_conf.setRange(0.05, 1.0)
        self.spin_conf.setSingleStep(0.05)
        self.spin_conf.setValue(0.85)
        self.spin_conf.valueChanged.connect(self.conf_changed.emit)
        self.spin_area = SpinBox()
        self.spin_area.setRange(0, 100000)
        self.spin_area.setValue(50)
        det.body.addLayout(form_row("置信度阈值", self.spin_conf, 160))
        det.body.addLayout(form_row("最小缺陷面积 (px)", self.spin_area, 160))
        col.addWidget(det)

        roi = Card("ROI设置")
        roi.body.setSpacing(4)
        # 用滚动区域包裹 ROI 列表，避免 ROI 多时挤压重叠
        self._roi_scroll = QScrollArea()
        self._roi_scroll.setWidgetResizable(True)
        self._roi_scroll.setFrameShape(QScrollArea.NoFrame)
        self._roi_scroll.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self._roi_scroll.setMinimumHeight(150)
        self._roi_container = QWidget()
        self._roi_rows = QVBoxLayout(self._roi_container)
        self._roi_rows.setSpacing(4)
        self._roi_rows.setContentsMargins(0, 0, 0, 0)
        self._roi_scroll.setWidget(self._roi_container)
        self._roi_scroll.setStyleSheet("QScrollArea{background:transparent;} QWidget{background:transparent;}")
        roi.body.addWidget(self._roi_scroll, 1)  # stretch=1 让滚动区填满卡片剩余空间
        btn_add = QPushButton("添加ROI")
        btn_add.setFixedHeight(34)
        btn_add.setStyleSheet("font-size:16px;")
        btn_add.clicked.connect(self._add_roi)
        roi.body.addWidget(btn_add)
        col.addWidget(roi, 1)  # ROI 卡片弹性伸展，占据剩余空间
        
        # 创建容器 widget 并设置布局
        container = QWidget()
        container.setLayout(col)
        scroll.setWidget(container)
        left_layout.addWidget(scroll, 1)  # 滚动区域占据剩余空间

        # 当前检测源提示（PC 本地 / Nano 下位机）
        self.lbl_source = QLabel("当前检测源: --")
        self.lbl_source.setWordWrap(True)
        self.lbl_source.setStyleSheet(
            "color:#22d3ee; font-size:14px; padding:6px 10px;"
            "background:#0f172a; border:1px solid #1e293b; border-radius:6px;")
        left_layout.addWidget(self.lbl_source)

        # 四个按钮固定在底部，不滚动
        btn_row = QHBoxLayout()
        btn_row.setContentsMargins(12, 0, 0, 0)
        self.btn_start = QPushButton("▶  开始检测")
        self.btn_start.setObjectName("btnPrimary")
        self.btn_start.setFixedHeight(40)
        self.btn_start.clicked.connect(self.start_requested)
        self.btn_stop = QPushButton("■  停止检测")
        self.btn_stop.setObjectName("btnDanger")
        self.btn_stop.setFixedHeight(40)
        self.btn_stop.clicked.connect(self.stop_requested)
        btn_row.addWidget(self.btn_start)
        btn_row.addWidget(self.btn_stop)
        left_layout.addLayout(btn_row)

        btn_row2 = QHBoxLayout()
        btn_row2.setContentsMargins(12, 0, 0, 0)
        self.btn_local_image = QPushButton("⌕ 本地图片")
        self.btn_local_image.setFixedHeight(40)
        self.btn_local_image.setToolTip(
            "单次模式：选择一张图片；多次模式：可选择多张图片批量检测")
        self.btn_local_image.clicked.connect(lambda: self.local_image_requested.emit())
        self.btn_save = QPushButton("◉  保存图像")
        self.btn_save.setFixedHeight(40)
        self.btn_save.clicked.connect(self._on_save_image)
        btn_row2.addWidget(self.btn_local_image)
        btn_row2.addWidget(self.btn_save)
        left_layout.addLayout(btn_row2)
        
        return left_layout

    # ---------- 中部预览 ----------
    def _build_center(self):
        col = QVBoxLayout()
        card = Card("实时预览")
        bar = QHBoxLayout()
        bar.setSpacing(6)
        self.preview = ImagePreview()
        self.preview.roi_edited.connect(self._on_preview_roi_edited)

        def tbtn(txt, tip, cb, w=34):
            b = QPushButton(txt)
            b.setObjectName("iconBtn")
            b.setFixedSize(w, 28)
            b.setToolTip(tip)
            b.clicked.connect(cb)
            bar.addWidget(b)
            return b

        tbtn("▶", "播放", lambda: self.start_requested.emit())
        tbtn("■", "停止", lambda: self.stop_requested.emit())
        tbtn("抓图", "抓图保存", self._on_save_image, 44)
        tbtn("全屏", "全屏预览", self._toggle_fullscreen, 44)
        bar.addStretch()
        tbtn("−", "缩小", lambda: self._zoom(-0.1))
        self.zoom_lbl = QLabel("100%")
        self.zoom_lbl.setStyleSheet("color:#94a3b8; background:transparent;")
        self.zoom_lbl.setFixedWidth(44)
        self.zoom_lbl.setAlignment(Qt.AlignCenter)
        bar.addWidget(self.zoom_lbl)
        tbtn("＋", "放大", lambda: self._zoom(0.1))
        bar.addStretch()
        # 多图导航（上一张/下一张）已移除（8-20）
        bar.addStretch()
        tbtn("网格", "网格", lambda: None, 44)
        tbtn("分屏", "分屏", lambda: None, 44)
        card.body.addLayout(bar)
        card.body.addWidget(self.preview, stretch=1)
        col.addWidget(card, stretch=1)
        return col

    # ---------- 右栏 ----------
    def _build_right(self):
        col = QVBoxLayout()
        card = Card("检测结果")

        kpi = QHBoxLayout()
        self.kpi_total = KPICard("总数", 0)
        self.kpi_ok = KPICard("OK", 0, "#22c55e")
        self.kpi_ng = KPICard("NG", 0, "#ef4444")
        self.kpi_yield = KPICard("良率", "--")
        for k in (self.kpi_total, self.kpi_ok, self.kpi_ng, self.kpi_yield):
            kpi.addWidget(k)
        card.body.addLayout(kpi)

        # 标题加大加粗
        title = QLabel("当前结果详情")
        title.setStyleSheet(
            "color:#f1f5f9; font-size:18px; font-weight:700; background:transparent;")
        card.body.addWidget(title)
        self.detail_table = StyledTable(["项目", "值"])
        self.detail_table.horizontalHeader().setVisible(False)
        self.detail_table.setColumnWidth(0, 120)
        self.detail_table.verticalHeader().setDefaultSectionSize(42)  # 行高加大
        self.detail_table.setWordWrap(True)  # 多缺陷类型文本自动换行
        from PyQt5.QtGui import QFont
        self._detail_rows = {}
        for key in ("产品", "结果", "缺陷类型", "面积 (px)", "置信度", "时间", "图像路径"):
            row = self.detail_table.rowCount()
            self.detail_table.insertRow(row)
            k = QTableWidgetItem(key)
            k.setForeground(QColor("#a5b4c8"))
            f = QFont()
            f.setPointSize(11)
            f.setBold(True)
            k.setFont(f)
            self.detail_table.setItem(row, 0, k)
            v = QTableWidgetItem("--")
            vf = QFont()
            vf.setPointSize(12)
            vf.setBold(True)
            v.setFont(vf)
            v.setForeground(QColor("#f1f5f9"))
            v.setTextAlignment(Qt.AlignLeft | Qt.AlignVCenter)
            self.detail_table.setItem(row, 1, v)
            # 缺陷类型行突出显示（更大更亮，支持多类型换行）
            if key == "缺陷类型":
                k.setForeground(QColor("#e2e8f0"))
                kf = QFont()
                kf.setPointSize(12)
                kf.setBold(True)
                k.setFont(kf)
                vf2 = QFont()
                vf2.setPointSize(14)
                vf2.setBold(True)
                v.setFont(vf2)
                self.detail_table.setRowHeight(row, 52)
            self._detail_rows[key] = v
        self.detail_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Fixed)
        self.detail_table.setColumnWidth(0, 120)
        card.body.addWidget(self.detail_table, stretch=1)
        col.addWidget(card, stretch=1)
        return col

    # ---------- 底部：历史 ----------
    def _build_history(self):
        col = QVBoxLayout()
        card = Card("检测历史")
        self.history_table = StyledTable(["时间", "产品", "结果", "缺陷类型", "图像路径"])
        card.body.addWidget(self.history_table, stretch=1)
        col.addWidget(card, stretch=1)
        return col

    # ---------- 底部：通信/存储/模型 ----------
    def _build_mid_bottom(self):
        col = QVBoxLayout()
        col.setSpacing(10)

        comm = Card("通信设置")
        # 下位机（推理服务）连接状态 + 手动重连
        inf_row = QHBoxLayout()
        inf_row.setSpacing(8)
        inf_lbl = QLabel("下位机(推理)")
        inf_lbl.setStyleSheet("color:#cbd5e1; font-size:16px; background:transparent;")
        inf_lbl.setFixedWidth(160)
        self.light_nano_mini = StatusLight("未连接")
        btn_reconnect = QPushButton("重新连接")
        btn_reconnect.setFixedHeight(32)
        btn_reconnect.setStyleSheet("font-size:15px;")
        btn_reconnect.clicked.connect(self.reconnect_requested)
        inf_row.addWidget(inf_lbl)
        inf_row.addWidget(self.light_nano_mini)
        inf_row.addStretch()
        inf_row.addWidget(btn_reconnect)
        comm.body.addLayout(inf_row)
        self.comm_tabs = QTabWidget()
        plc_tab = QWidget()
        pl = QVBoxLayout(plc_tab)
        pl.setContentsMargins(6, 6, 6, 6)
        self.combo_proto = FocusComboBox()
        self.combo_proto.addItems(["Modbus TCP"])
        self.edit_plc_ip = QLineEdit("192.168.1.200")
        self.spin_plc_port = SpinBox()
        self.spin_plc_port.setRange(1, 65535)
        self.spin_plc_port.setValue(2000)
        self.spin_plc_hb = SpinBox()
        self.spin_plc_hb.setRange(100, 10000)
        self.spin_plc_hb.setValue(500)
        self.light_plc_mini = StatusLight("已连接")
        for lbl, w in (("通信协议", self.combo_proto), ("服务器IP", self.edit_plc_ip),
                       ("端口号", self.spin_plc_port), ("心跳周期 (ms)", self.spin_plc_hb)):
            pl.addLayout(form_row(lbl, w))
        pl.addWidget(self.light_plc_mini)
        ser_tab = QWidget()
        sl = QVBoxLayout(ser_tab)
        sl.setContentsMargins(6, 6, 6, 6)
        sl.addWidget(_lbl("串口参数在「通信设置」页配置"))
        self.comm_tabs.addTab(plc_tab, "PLC通信")
        self.comm_tabs.addTab(ser_tab, "串口通信")
        comm.body.addWidget(self.comm_tabs)
        col.addWidget(comm)

        row = QHBoxLayout()
        store = Card("存储设置")
        self.edit_save_path = QLineEdit("D:/Inspect/Images")
        self.edit_save_path.setReadOnly(True)
        btn_browse = QPushButton("…")
        btn_browse.setObjectName("iconBtn")
        btn_browse.setFixedSize(36, 28)
        btn_browse.setToolTip("选择保存目录")
        btn_browse.clicked.connect(self._browse_save)
        save_brow = QHBoxLayout()
        save_brow.setSpacing(4)
        save_brow.addWidget(self.edit_save_path, 1)
        save_brow.addWidget(btn_browse)
        self.combo_clean = FocusComboBox()
        self.combo_clean.addItems(["磁盘空间 < 10% 时删除", "保留最近 30 天", "不清理"])
        store.body.addLayout(form_row("保存路径", save_brow, 70))
        store.body.addLayout(form_row("自动清理", self.combo_clean, 70))
        row.addWidget(store)

        model = Card("模型设置")
        self.combo_model = FocusComboBox()
        self.combo_model.addItems(["Product_A_v1"])
        btn_load = QPushButton("加载模型")
        btn_load.setToolTip("选择并加载检测模型（查看详情 / 切换模型）")
        btn_load.clicked.connect(
            lambda _=False: self.load_model_requested.emit(self.combo_model.currentText()))
        btn_mgr = QPushButton("模型管理")
        btn_mgr.setToolTip("添加 / 扫描 / 删除 / 配置模型")
        btn_mgr.clicked.connect(self.model_mgr_requested)
        mrow = QHBoxLayout()
        mrow.addWidget(btn_load)
        mrow.addWidget(btn_mgr)
        model.body.addLayout(form_row("当前模型", self.combo_model, 70))
        model.body.addLayout(mrow)
        row.addWidget(model)
        col.addLayout(row)
        col.addStretch()
        return col

    # ---------- 底部：mini 日志 ----------
    def _build_mini_log(self):
        col = QVBoxLayout()
        card = Card("运行日志")
        self.mini_log = QTextEdit()
        self.mini_log.setReadOnly(True)
        card.body.addWidget(self.mini_log, stretch=1)
        # 批量检测进度条（放在日志文本下方）
        self.lbl_batch_prog = QLabel("")
        self.lbl_batch_prog.setStyleSheet("color:#22d3ee; font-size:13px; background:transparent;")
        self.lbl_batch_prog.setVisible(False)
        card.body.addWidget(self.lbl_batch_prog)
        self.bar_batch = QProgressBar()
        self.bar_batch.setFixedHeight(8)
        self.bar_batch.setTextVisible(False)
        self.bar_batch.setVisible(False)
        self.bar_batch.setStyleSheet(
            "QProgressBar{background:#1e293b; border:none; border-radius:4px;}"
            "QProgressBar::chunk{background:qlineargradient("
            "x1:0,y1:0,x2:1,y2:0, stop:0 #22d3ee, stop:1 #3b82f6); border-radius:4px;}")
        card.body.addWidget(self.bar_batch)
        col.addWidget(card, stretch=1)
        return col

    # ================= ROI =================
    def set_rois(self, rois: list):
        self._rois = rois
        self._rebuild_roi_rows()
        self.preview.set_rois(rois)

    def set_cur_model(self, name: str):
        if self.combo_model.findText(name) < 0:
            self.combo_model.addItem(name)
        self.combo_model.setCurrentText(name)

    def set_running(self, running: bool):
        """同步开始/停止按钮的启用状态和显示文本，避免用户重复启动"""
        self.btn_start.setEnabled(not running)
        self.btn_stop.setEnabled(running)
        self.btn_start.setText("● 检测中..." if running else "▶  开始检测")

    def _on_preview_roi_edited(self, idx: int, roi: dict):
        if 0 <= idx < len(self._rois):
            self._rois[idx] = roi
            self.preview.set_rois(self._rois)
            self.roi_changed.emit(self._rois)

    def _browse_save(self):
        d = QFileDialog.getExistingDirectory(self, "选择保存目录", self.edit_save_path.text())
        if d:
            self.edit_save_path.setText(d)
            self.save_path_changed.emit(d)

    def get_rois(self) -> list:
        return self._rois

    def _rebuild_roi_rows(self):
        # 清除所有项目（包括弹簧）
        while self._roi_rows.count():
            item = self._roi_rows.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
            elif item.layout():
                while item.layout().count():
                    sub = item.layout().takeAt(0)
                    if sub.widget():
                        sub.widget().deleteLater()
        # 重建 ROI 行
        for i, roi in enumerate(self._rois):
            row = QHBoxLayout()
            row.setSpacing(8)
            lbl = QLabel(f"ROI {i + 1}")
            lbl.setStyleSheet("color:#cbd5e1; font-size:18px; background:transparent;")
            lbl.setFixedWidth(160)
            row.addWidget(lbl)
            combo = FocusComboBox()
            combo.addItems(["启用", "禁用"])
            combo.setCurrentIndex(0 if roi.get("enabled", True) else 1)
            combo.currentIndexChanged.connect(
                lambda idx, r=roi: self._on_roi_toggle(r, idx == 0))
            row.addWidget(combo, 1)
            be = QPushButton("编辑")
            be.setObjectName("iconBtn")
            be.setFixedSize(48, 30)
            be.clicked.connect(lambda _=False, r=roi: self._edit_roi(r))
            bd = QPushButton("删除")
            bd.setObjectName("iconBtn")
            bd.setFixedSize(48, 30)
            bd.clicked.connect(lambda _=False, r=roi: self._del_roi(r))
            row.addWidget(be)
            row.addWidget(bd)
            self._roi_rows.addLayout(row)
        # 底部弹簧，让 ROI 顶对齐
        self._roi_rows.addStretch()

    def _on_roi_toggle(self, roi, enabled: bool):
        roi["enabled"] = enabled
        self.preview.set_rois(self._rois)
        self.roi_changed.emit(self._rois)

    def _edit_roi(self, roi):
        dlg = RoiEditDialog(roi, self, image=self.preview.get_image())
        if dlg.exec_() == QDialog.Accepted:
            roi.update(dlg.values())
            self.preview.set_rois(self._rois)
            self.roi_changed.emit(self._rois)

    def _del_roi(self, roi):
        if roi in self._rois:
            self._rois.remove(roi)
            self._rebuild_roi_rows()
            self.preview.set_rois(self._rois)
            self.roi_changed.emit(self._rois)

    def _add_roi(self):
        n = len(self._rois)
        # 每个新 ROI 偏移 60px，避免全部叠在同一位置
        offset = n * 60
        self._rois.append({"name": f"ROI {n + 1}", "enabled": True,
                           "x": 80 + offset, "y": 80 + offset, "w": 200, "h": 200})
        self._rebuild_roi_rows()
        self.preview.set_rois(self._rois)
        self.roi_changed.emit(self._rois)

    # ================= 数据更新 =================
    def update_image(self, qimg):
        self.preview.set_image(qimg)

    def update_detections(self, dets: list):
        self.preview.set_detections(dets)

    def update_kpi(self, total, ok, ng, yield_rate):
        self.kpi_total.set_value(total)
        self.kpi_ok.set_value(ok)
        self.kpi_ng.set_value(ng)
        self.kpi_yield.set_value(f"{yield_rate:.1f}%")

    def update_detail(self, product, result, defect_type, area, conf, ts, path):
        from PyQt5.QtGui import QColor
        vals = {"产品": product, "结果": result, "缺陷类型": defect_type,
                "面积 (px)": area, "置信度": conf, "时间": ts, "图像路径": path}
        for k, v in vals.items():
            item = self._detail_rows[k]
            item.setText(str(v))
            if k == "结果":
                item.setForeground(QColor("#ef4444") if v == "NG" else QColor("#22c55e"))

    def add_history(self, values: list):
        colors = {2: "#ef4444"} if values[2] == "NG" else {2: "#22c55e"}
        self.history_table.add_row(values, colors)

    def append_mini_log(self, level: str, msg: str):
        import time as _t
        import html as _html
        color = {"INFO": "#22c55e", "WARN": "#f59e0b",
                 "ERROR": "#ef4444"}.get(level, "#94a3b8")
        ts = _t.strftime("%H:%M:%S")
        # msg 可能含 <>&（路径/模型名/串口内容），转义防 HTML 注入
        safe = _html.escape(str(msg), quote=False)
        self.mini_log.append(
            f'<span style="color:#64748b">{ts}</span> '
            f'<span style="color:{color}">[{_html.escape(level)}] {safe}</span>')
        self.mini_log.verticalScrollBar().setValue(
            self.mini_log.verticalScrollBar().maximum())

    def update_batch_progress(self, done: int, total: int):
        """更新批量检测进度条"""
        if total <= 0:
            self.bar_batch.setVisible(False)
            self.lbl_batch_prog.setVisible(False)
            return
        self.bar_batch.setVisible(True)
        self.lbl_batch_prog.setVisible(True)
        pct = int(done / total * 100)
        self.bar_batch.setValue(pct)
        if done >= total:
            self.lbl_batch_prog.setText(f"批量检测完成 {done}/{total} 张 ✅")
            self.lbl_batch_prog.setStyleSheet("color:#22c55e; font-size:13px; background:transparent;")
        else:
            self.lbl_batch_prog.setText(f"批量检测中 {done}/{total} 张...")
            self.lbl_batch_prog.setStyleSheet("color:#22d3ee; font-size:13px; background:transparent;")

    def set_plc_light(self, on: bool):
        self.light_plc_mini.set_status(1 if on else 0, "已连接" if on else "未连接")

    # ================= 事件 =================
    def _on_save_image(self):
        path, _ = QFileDialog.getSaveFileName(self, "保存图像", "capture.png",
                                              "PNG (*.png)")
        if path:
            self.save_image_requested.emit(path)

    def _zoom(self, delta: float):
        self.preview.zoom_by(delta)
        self.zoom_lbl.setText(f"{int(self.preview.get_zoom() * 100)}%")

    def _toggle_fullscreen(self):
        if self.preview.isFullScreen():
            self.preview.showNormal()
        else:
            self.preview.showFullScreen()

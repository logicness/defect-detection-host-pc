"""
P2 参数设置页（设计稿图 2）
左：相机参数；中：检测模型 + 图像预处理；右：ROI设置(表格+预览) + 存储设置
底部：应用设置 / 保存配置 / 恢复默认
卡片垂直撑满、字体 18px、输入框加高、间距宽松
"""
from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QLineEdit, QTableWidget, QTableWidgetItem,
    QHeaderView, QFileDialog, QDialog, QSizePolicy,
    QMessageBox
)
from PyQt5.QtCore import Qt, pyqtSignal

from components.common_widgets import Card, Toggle, form_row, SpinBox, DoubleSpinBox, FocusComboBox
from components.image_preview import ImagePreview
from components.roi_editor import RoiEditDialog

_INPUT_H = 36  # 输入框统一高度


def _style_input(w):
    """统一输入框样式：36px 高、16px 字号"""
    w.setFixedHeight(_INPUT_H)
    w.setStyleSheet("font-size:16px;")
    return w


def _lbl(text):
    l = QLabel(text)
    l.setStyleSheet("color:#cbd5e1; font-size:18px; background:transparent;")
    return l


class ParamSettingPage(QWidget):
    params_apply_requested = pyqtSignal(float, float)   # conf, iou
    save_config_requested = pyqtSignal(dict)
    reset_requested = pyqtSignal()
    load_model_requested = pyqtSignal(str)
    model_mgr_requested = pyqtSignal()       # 打开模型管理对话框
    roi_changed = pyqtSignal(list)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._rois = []
        self._build()

    def _build(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(14, 14, 14, 14)
        root.setSpacing(16)

        top = QHBoxLayout()
        top.setSpacing(16)
        top.addLayout(self._build_camera(), 3)
        top.addLayout(self._build_model(), 4)
        top.addLayout(self._build_right(), 4)
        root.addLayout(top, 1)

        # 底部按钮条
        bar = QHBoxLayout()
        bar.addStretch()
        self.btn_apply = QPushButton("✓  应用设置")
        self.btn_apply.setObjectName("btnPrimary")
        self.btn_apply.setFixedSize(220, 48)
        self.btn_apply.clicked.connect(self._on_apply)
        self.btn_save = QPushButton("保存配置")
        self.btn_save.setFixedSize(220, 48)
        self.btn_save.clicked.connect(lambda: self.save_config_requested.emit(self.get_config()))
        self.btn_reset = QPushButton("↺  恢复默认")
        self.btn_reset.setFixedSize(220, 48)
        self.btn_reset.clicked.connect(self.reset_requested)
        bar.addWidget(self.btn_apply)
        bar.addSpacing(32)
        bar.addWidget(self.btn_save)
        bar.addSpacing(32)
        bar.addWidget(self.btn_reset)
        bar.addStretch()
        root.addLayout(bar)

    # ---------- 左：相机参数 ----------
    def _build_camera(self):
        col = QVBoxLayout()
        col.setSpacing(16)
        card = Card("相机参数")
        card.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

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
        self.combo_trigger = FocusComboBox()
        self.combo_trigger.addItems(["连续触发", "硬件触发", "软件触发"])
        self.combo_fps = FocusComboBox()
        self.combo_fps.addItems(["30 FPS", "15 FPS", "60 FPS"])

        for lbl, w in (("相机选择", self.combo_camera), ("曝光时间 (ms)", self.spin_exposure),
                       ("增益 (dB)", self.spin_gain), ("光源亮度", self.spin_bright),
                       ("触发模式", self.combo_trigger), ("采集帧率", self.combo_fps)):
            _style_input(w)
            card.body.addLayout(form_row(lbl, w, 160))

        # 让内容均匀撑满卡片
        card.body.addStretch()
        col.addWidget(card, 1)  # stretch=1 让卡片撑满垂直空间
        return col

    # ---------- 中：检测模型 + 预处理 ----------
    def _build_model(self):
        col = QVBoxLayout()
        col.setSpacing(16)

        m = Card("检测模型")
        m.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.edit_cur_model = QLineEdit("Product_A_v1")
        self.edit_cur_model.setReadOnly(True)
        self.edit_model_file = QLineEdit()
        self.edit_model_file.setPlaceholderText("C:/models/Product_A_v1.onnx")
        _style_input(self.edit_cur_model)
        _style_input(self.edit_model_file)
        btn_browse = QPushButton("…")
        btn_browse.setObjectName("iconBtn")
        btn_browse.setFixedHeight(_INPUT_H)
        btn_browse.setFixedWidth(36)
        btn_browse.clicked.connect(self._browse_model)
        frow = QHBoxLayout()
        frow.setSpacing(4)
        frow.addWidget(self.edit_model_file, 1)
        frow.addWidget(btn_browse)
        self.combo_input = FocusComboBox()
        self.combo_input.addItems(["640×640", "320×320", "1280×1280"])
        self.spin_conf = DoubleSpinBox()
        self.spin_conf.setRange(0.05, 1.0)
        self.spin_conf.setSingleStep(0.05)
        self.spin_conf.setValue(0.85)
        self.spin_area = SpinBox()
        self.spin_area.setRange(0, 100000)
        self.spin_area.setValue(50)
        for w in (self.combo_input, self.spin_conf, self.spin_area):
            _style_input(w)
        m.body.addLayout(form_row("当前模型", self.edit_cur_model, 160))
        m.body.addLayout(form_row("模型文件", frow, 160))
        m.body.addLayout(form_row("输入尺寸", self.combo_input, 160))
        m.body.addLayout(form_row("置信度阈值", self.spin_conf, 160))
        m.body.addLayout(form_row("最小缺陷面积 (px)", self.spin_area, 160))
        btn_load = QPushButton("加载模型")
        btn_load.setObjectName("btnPrimary")
        btn_load.setFixedHeight(40)
        btn_load.setStyleSheet("font-size:16px;")
        btn_load.setToolTip("选择并加载检测模型（查看详情 / 切换模型）")
        btn_load.clicked.connect(
            lambda _=False: self.load_model_requested.emit(self.edit_cur_model.text()))
        mrow = QHBoxLayout()
        mrow.addStretch()
        mrow.addWidget(btn_load)
        mrow.addStretch()
        m.body.addLayout(mrow)
        m.body.addStretch()
        col.addWidget(m, 3)  # 模型卡占 3 份

        p = Card("图像预处理")
        p.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.combo_resize = FocusComboBox()
        self.combo_resize.addItems(["不缩放（保持原始）", "缩放至 640×640", "缩放至 1280×1280"])
        self.tg_gray = Toggle(True)
        self.combo_denoise = FocusComboBox()
        self.combo_denoise.addItems(["中值滤波（3×3）", "高斯滤波（3×3）", "不去噪"])
        self.tg_contrast = Toggle(True)
        for w in (self.combo_resize, self.combo_denoise):
            _style_input(w)
        for lbl, w in (("尺寸调整", self.combo_resize), ("灰度归一化", self.tg_gray),
                       ("去噪", self.combo_denoise), ("对比度增强", self.tg_contrast)):
            p.body.addLayout(form_row(lbl, w, 160))
        p.body.addStretch()
        col.addWidget(p, 2)  # 预处理卡占 2 份
        return col

    # ---------- 右：ROI + 存储 ----------
    def _build_right(self):
        col = QVBoxLayout()
        col.setSpacing(16)

        roi = Card("ROI设置")
        roi.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.roi_table = QTableWidget()
        self.roi_table.setColumnCount(4)
        self.roi_table.setHorizontalHeaderLabels(["ROI", "启用", "坐标 (x, y, w, h)", "操作"])
        self.roi_table.verticalHeader().setVisible(False)
        self.roi_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.roi_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        roi.body.addWidget(self.roi_table)
        # 添加 ROI 按钮：全宽、加高、往下留间距
        btn_add = QPushButton("添加ROI")
        btn_add.setFixedHeight(38)
        btn_add.setStyleSheet("font-size:16px;")
        btn_add.clicked.connect(self._add_roi)
        roi.body.addSpacing(8)
        roi.body.addWidget(btn_add)
        roi.body.addSpacing(8)
        self.preview = ImagePreview()
        self.preview.setMinimumHeight(220)
        roi.body.addWidget(self.preview, 1)
        col.addWidget(roi, 5)  # ROI 卡占 5 份（更大，看清全部）

        st = Card("存储设置")
        st.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.edit_save_path = QLineEdit("D:/Inspect/Images")
        _style_input(self.edit_save_path)
        brow = QHBoxLayout()
        brow.setSpacing(4)
        brow.addWidget(self.edit_save_path, 1)
        bb = QPushButton("…")
        bb.setObjectName("iconBtn")
        bb.setFixedHeight(_INPUT_H)
        bb.setFixedWidth(36)
        bb.clicked.connect(self._browse_save)
        brow.addWidget(bb)
        self.tg_save_ng = Toggle(True)
        self.tg_save_orig = Toggle(True)
        self.combo_clean = FocusComboBox()
        self.combo_clean.addItems(["磁盘空间 < 10% 时删除", "保留最近 30 天", "不清理"])
        _style_input(self.combo_clean)
        st.body.addLayout(form_row("保存路径", brow, 160))
        st.body.addLayout(form_row("保存NG图像", self.tg_save_ng, 160))
        st.body.addLayout(form_row("保存原图", self.tg_save_orig, 160))
        st.body.addLayout(form_row("自动清理", self.combo_clean, 160))
        st.body.addStretch()
        col.addWidget(st, 1)  # 存储卡占 1 份（紧凑）
        return col

    # ================= ROI =================
    def set_rois(self, rois: list):
        self._rois = rois
        self._rebuild_table()
        self.preview.set_rois(rois)

    def get_rois(self) -> list:
        return self._rois

    def _rebuild_table(self):
        self.roi_table.setRowCount(0)
        for i, r in enumerate(self._rois):
            row = self.roi_table.rowCount()
            self.roi_table.insertRow(row)
            self.roi_table.setItem(row, 0, QTableWidgetItem(f"ROI {i + 1}"))
            tg = Toggle(r.get("enabled", True))
            tg.toggled_sig.connect(lambda on, rr=r: self._toggle_roi(rr, on))
            self.roi_table.setCellWidget(row, 1, tg)
            self.roi_table.setItem(
                row, 2, QTableWidgetItem(f"{r['x']}, {r['y']}, {r['w']}, {r['h']}"))
            be = QPushButton("编辑")
            be.setObjectName("iconBtn")
            be.setFixedSize(50, 28)
            be.clicked.connect(lambda _=False, rr=r: self._edit_roi(rr))
            bd = QPushButton("删除")
            bd.setObjectName("iconBtn")
            bd.setFixedSize(50, 28)
            bd.clicked.connect(lambda _=False, rr=r: self._delete_roi(rr))
            op = QWidget()
            op_layout = QHBoxLayout(op)
            op_layout.setContentsMargins(4, 2, 4, 2)
            op_layout.setSpacing(8)
            op_layout.addStretch()
            op_layout.addWidget(be)
            op_layout.addWidget(bd)
            op_layout.addStretch()
            self.roi_table.setCellWidget(row, 3, op)
        self._render_preview()

    def _render_preview(self):
        """用模拟帧源渲染一张示例图作为 ROI 预览底图"""
        from core.frame_source import SimFrameSource
        from PyQt5.QtGui import QImage
        src = SimFrameSource()
        src.open()
        _, frame = src.read()
        if frame is not None:
            import cv2
            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            h, w, ch = rgb.shape
            self.preview.set_image(
                QImage(rgb.data, w, h, ch * w, QImage.Format_RGB888).copy())
        self.preview.set_rois(self._rois)

    def _toggle_roi(self, roi, on: bool):
        roi["enabled"] = on
        self.preview.set_rois(self._rois)
        self.roi_changed.emit(self._rois)

    def _edit_roi(self, roi):
        dlg = RoiEditDialog(roi, self, image=self.preview.get_image())
        if dlg.exec_() == QDialog.Accepted:
            roi.update(dlg.values())
            self._rebuild_table()
            self.roi_changed.emit(self._rois)

    def _delete_roi(self, roi):
        idx = self._rois.index(roi)
        name = roi.get("name", f"ROI {idx + 1}")
        ret = QMessageBox.question(
            self, "确认删除", f"确定要删除 <b>{name}</b> 吗?\n删除后不可恢复。",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if ret == QMessageBox.Yes:
            self._rois.remove(roi)
            self._rebuild_table()
            self.roi_changed.emit(self._rois)

    def _add_roi(self):
        n = len(self._rois)
        offset = n * 60
        self._rois.append({"name": f"ROI {n + 1}", "enabled": True,
                           "x": 80 + offset, "y": 80 + offset, "w": 200, "h": 200})
        self._rebuild_table()
        self.roi_changed.emit(self._rois)

    # ================= 配置 =================
    def set_cur_model(self, name: str):
        self.edit_cur_model.setText(name)

    def get_config(self) -> dict:
        return {
            "camera": {"name": self.combo_camera.currentText(),
                       "exposure": self.spin_exposure.value(),
                       "gain": self.spin_gain.value(),
                       "brightness": self.spin_bright.value(),
                       "trigger": self.combo_trigger.currentText(),
                       "fps": self.combo_fps.currentText()},
            "detect": {"conf": self.spin_conf.value(), "min_area": self.spin_area.value(),
                       "model": self.edit_cur_model.text(),
                       "model_file": self.edit_model_file.text(),
                       "input_size": self.combo_input.currentText()},
            "preprocess": {"resize": self.combo_resize.currentText(),
                           "gray_norm": self.tg_gray.isChecked(),
                           "denoise": self.combo_denoise.currentText(),
                           "contrast": self.tg_contrast.isChecked()},
            "storage": {"save_path": self.edit_save_path.text(),
                        "save_ng": self.tg_save_ng.isChecked(),
                        "save_orig": self.tg_save_orig.isChecked(),
                        "auto_clean": self.combo_clean.currentText()},
            "rois": self._rois,
        }

    def apply_config(self, cfg: dict):
        cam = cfg.get("camera", {})
        if cam:
            self.combo_camera.setCurrentText(cam.get("name", "相机 01"))
            self.spin_exposure.setValue(cam.get("exposure", 10.0))
            self.spin_gain.setValue(cam.get("gain", 2.0))
            self.spin_bright.setValue(cam.get("brightness", 128))
            self.combo_trigger.setCurrentText(cam.get("trigger", "连续触发"))
            self.combo_fps.setCurrentText(cam.get("fps", "30 FPS"))
        det = cfg.get("detect", {})
        if det:
            self.spin_conf.setValue(det.get("conf", 0.85))
            self.spin_area.setValue(det.get("min_area", 50))
            self.edit_cur_model.setText(det.get("model", "Product_A_v1"))
            self.edit_model_file.setText(det.get("model_file", ""))
            self.combo_input.setCurrentText(det.get("input_size", "640×640"))
        pre = cfg.get("preprocess", {})
        if pre:
            self.combo_resize.setCurrentText(pre.get("resize", "不缩放（保持原始）"))
            self.tg_gray.setChecked(pre.get("gray_norm", True))
            self.combo_denoise.setCurrentText(pre.get("denoise", "中值滤波（3×3）"))
            self.tg_contrast.setChecked(pre.get("contrast", True))
        st = cfg.get("storage", {})
        if st:
            self.edit_save_path.setText(st.get("save_path", "D:/Inspect/Images"))
            self.tg_save_ng.setChecked(st.get("save_ng", True))
            self.tg_save_orig.setChecked(st.get("save_orig", True))
            self.combo_clean.setCurrentText(st.get("auto_clean", "磁盘空间 < 10% 时删除"))
        if "rois" in cfg:
            self.set_rois(cfg["rois"])

    # ================= 事件 =================
    def _on_apply(self):
        self.params_apply_requested.emit(self.spin_conf.value(), 0.45)

    def _browse_model(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "选择模型文件", "", "模型 (*.onnx *.pt *.engine)")
        if path:
            self.edit_model_file.setText(path)

    def _browse_save(self):
        d = QFileDialog.getExistingDirectory(self, "选择保存目录", "D:/")
        if d:
            self.edit_save_path.setText(d)

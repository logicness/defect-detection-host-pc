"""
自定义标题栏（无边框窗口）
左：应用标题；右：相机/PLC/模型 三状态灯 + 当前模型/相机标签 + 汉堡菜单
"""
from PyQt5.QtWidgets import QWidget, QHBoxLayout, QLabel, QPushButton, QFrame
from PyQt5.QtCore import Qt, pyqtSignal, QPropertyAnimation, QPoint

from components.common_widgets import StatusLight


class TitleBar(QWidget):
    """48px 高标题栏，支持拖动移动窗口"""

    window_minimized = pyqtSignal()
    window_maximized = pyqtSignal()
    window_closed = pyqtSignal()
    menu_requested = pyqtSignal()

    def __init__(self, title: str, parent=None):
        super().__init__(parent)
        self.setFixedHeight(52)
        self.setStyleSheet("background-color:#10151f; border-bottom:1px solid #1f2937;")
        self._drag_pos = None

        lay = QHBoxLayout(self)
        lay.setContentsMargins(16, 0, 12, 0)
        lay.setSpacing(12)

        t = QLabel(title)
        t.setStyleSheet("color:#f1f5f9; font-size:20px; font-weight:700; background:transparent;")
        lay.addWidget(t)
        lay.addStretch()

        # 三状态灯（图 1 顶部：相机已连接 / PLC已连接 / 模型已加载）
        self.light_camera = StatusLight("相机未连接")
        self.light_plc = StatusLight("PLC未连接")
        self.light_model = StatusLight("模型未加载")
        for w in (self.light_camera, self.light_plc, self.light_model):
            w.setFixedWidth(130)
            lay.addWidget(w)

        # 标签 chips
        self.tag_model = QLabel("当前模型: --")
        self.tag_camera = QLabel("相机: --")
        for tag in (self.tag_model, self.tag_camera):
            tag.setStyleSheet(
                "background:#1e293b; color:#cbd5e1; border:1px solid #334155;"
                "border-radius:4px; padding:5px 12px; font-size:15px;")
            lay.addWidget(tag)

        # 窗口控制 + 菜单
        for txt, tip, sig in (("—", "最小化", self.window_minimized),
                              ("□", "最大化", self.window_maximized),
                              ("✕", "关闭", self.window_closed)):
            b = QPushButton(txt)
            b.setFixedSize(40, 34)
            b.setToolTip(tip)
            b.setStyleSheet(
                "QPushButton{border:none; background:transparent; color:#94a3b8; font-size:16px;}"
                "QPushButton:hover{background:#1e293b; color:#e2e8f0;}")
            b.clicked.connect(sig)
            lay.addWidget(b)

        menu = QPushButton("≡")
        menu.setFixedSize(40, 34)
        menu.setToolTip("菜单")
        menu.setStyleSheet(
            "QPushButton{border:none; background:transparent; color:#e2e8f0; font-size:20px;}"
            "QPushButton:hover{background:#1e293b;}")
        menu.clicked.connect(self.menu_requested)
        lay.addWidget(menu)

    # ---------- 标签/状态接口 ----------
    def set_model_tag(self, name: str):
        self.tag_model.setText(f"当前模型: {name}")

    def set_camera_tag(self, name: str):
        self.tag_camera.setText(f"相机: {name}")

    def set_status(self, camera: int = 0, plc: int = 0, model: int = 0):
        self.light_camera.set_status(
            camera, "相机已连接" if camera == 1 else ("相机故障" if camera == 2 else "相机未连接"))
        self.light_plc.set_status(
            plc, "PLC已连接" if plc == 1 else ("PLC故障" if plc == 2 else "PLC未连接"))
        self.light_model.set_status(
            model, "模型已加载" if model == 1 else "模型未加载")
        # 更新 tag 标签
        if model == 1:
            self.tag_model.setText(f"当前模型: {self.tag_model.text().split(': ', 1)[-1] if ': ' in self.tag_model.text() and self.tag_model.text().split(': ', 1)[-1] != '--' else '--'}")
        if camera == 0:
            self.tag_camera.setText("相机: --")

    # ---------- 拖动移动 ----------
    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._drag_pos = event.globalPos() - self.window().frameGeometry().topLeft()
            event.accept()

    def mouseMoveEvent(self, event):
        if self._drag_pos is not None:
            self.window().move(event.globalPos() - self._drag_pos)
            event.accept()

    def mouseReleaseEvent(self, event):
        self._drag_pos = None

    def mouseDoubleClickEvent(self, event):
        self.window_maximized.emit()

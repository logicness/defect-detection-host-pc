"""
公共组件库
Card / KPICard / StatusLight / Toggle / StyledTable / SegGroup / form 行助手
全部按 5 张设计稿的暗色风格实现
"""
from PyQt5.QtWidgets import (
    QFrame, QVBoxLayout, QHBoxLayout, QLabel, QTableWidget, QTableWidgetItem,
    QPushButton, QAbstractButton, QHeaderView, QWidget, QSizePolicy
)
from PyQt5.QtCore import Qt, pyqtSignal, QSize
from PyQt5.QtGui import QPainter, QColor, QPen, QBrush, QFont


# ---------- 卡片 ----------
class Card(QFrame):
    """带标题的圆角卡片容器，内容放入 self.body 布局"""

    def __init__(self, title: str = "", parent=None):
        super().__init__(parent)
        self.setObjectName("card")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(12, 10, 12, 10)
        outer.setSpacing(8)
        if title:
            t = QLabel(title)
            t.setObjectName("cardTitle")
            t.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
            outer.addWidget(t)
        self.body = QVBoxLayout()
        self.body.setContentsMargins(0, 0, 0, 0)
        self.body.setSpacing(8)
        outer.addLayout(self.body)


# ---------- KPI 数字卡 ----------
class KPICard(QFrame):
    """标题 + 大数字（可配色），用于 总数/OK/NG/良率 等"""

    def __init__(self, title: str, value: str = "0", color: str = "#e2e8f0",
                 parent=None):
        super().__init__(parent)
        self.setObjectName("card")
        self.setMaximumHeight(120)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(10, 8, 10, 8)
        lay.setSpacing(4)
        self.title_lbl = QLabel(title)
        self.title_lbl.setAlignment(Qt.AlignCenter)
        self.title_lbl.setStyleSheet("color:#94a3b8; font-size:15px; background:transparent; border:none;")
        self.value_lbl = QLabel(str(value))
        self.value_lbl.setAlignment(Qt.AlignCenter)
        self.value_lbl.setStyleSheet(
            f"color:{color}; font-size:44px; font-weight:700; background:transparent; border:none;")
        lay.addWidget(self.title_lbl)
        lay.addWidget(self.value_lbl)

    def set_value(self, value, color: str = None):
        self.value_lbl.setText(str(value))
        if color:
            self.value_lbl.setStyleSheet(
                f"color:{color}; font-size:44px; font-weight:700; background:transparent; border:none;")


# ---------- 状态灯 ----------
class StatusLight(QWidget):
    """圆点状态灯 + 文字（绿=连接/正常，灰=断开，红=故障）"""

    def __init__(self, text: str = "", parent=None):
        super().__init__(parent)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(8)
        self.dot = QLabel("●")
        self.dot.setStyleSheet("color:#475569; font-size:15px; background:transparent;")
        self.txt = QLabel(text)
        self.txt.setStyleSheet("color:#cbd5e1; font-size:15px; font-weight:600; background:transparent;")
        lay.addWidget(self.dot)
        lay.addWidget(self.txt)
        lay.addStretch()

    def set_status(self, on: int, text: str = None):
        """on: 0=灰(断开) 1=绿(正常) 2=红(故障)"""
        color = {0: "#475569", 1: "#22c55e", 2: "#ef4444"}.get(on, "#475569")
        self.dot.setStyleSheet(f"color:{color}; font-size:15px; background:transparent;")
        if text is not None:
            self.txt.setText(text)


# ---------- 拨动开关 ----------
class Toggle(QAbstractButton):
    """iOS 风格拨动开关（设计稿中的蓝色 Toggle）"""

    toggled_sig = pyqtSignal(bool)

    def __init__(self, checked: bool = True, parent=None):
        super().__init__(parent)
        self.setCheckable(True)
        self.setChecked(checked)
        self.setFixedSize(48, 26)
        self.setCursor(Qt.PointingHandCursor)
        self.toggled.connect(self.toggled_sig)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        on = self.isChecked()
        track = QColor("#2563eb") if on else QColor("#334155")
        p.setPen(Qt.NoPen)
        p.setBrush(QBrush(track))
        p.drawRoundedRect(0, 2, self.width(), self.height() - 4, 9, 9)
        # 滑钮
        r = self.height() - 8
        x = self.width() - r - 4 if on else 4
        p.setBrush(QBrush(QColor("#ffffff")))
        p.drawEllipse(x, 4, r, r)


# ---------- 暗色表格 ----------
class StyledTable(QTableWidget):
    """设计稿风格表格：暗色 + 表头加粗 + 行数据助手"""

    def __init__(self, headers: list, parent=None):
        super().__init__(parent)
        self.setColumnCount(len(headers))
        self.setHorizontalHeaderLabels(headers)
        self.setEditTriggers(QTableWidget.NoEditTriggers)
        self.setSelectionBehavior(QTableWidget.SelectRows)
        self.setAlternatingRowColors(True)
        self.verticalHeader().setVisible(False)
        self.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.setShowGrid(False)

    def add_row(self, values: list, colors: dict = None) -> int:
        """追加一行；colors={col_index: '#hex'} 给指定列上色（如 OK 绿 / NG 红）"""
        row = self.rowCount()
        self.insertRow(row)
        self.set_row_data(row, values, colors)
        return row

    def set_row_data(self, row: int, values: list, colors: dict = None):
        for c, v in enumerate(values):
            item = QTableWidgetItem(str(v))
            item.setTextAlignment(Qt.AlignCenter)
            if colors and c in colors:
                item.setForeground(QColor(colors[c]))
            self.setItem(row, c, item)

    def clear_rows(self):
        self.setRowCount(0)


# ---------- 分段按钮组 ----------
class SegGroup(QWidget):
    """互斥分段按钮（全部 / OK / NG 等），selected 信号携带文字"""

    selected = pyqtSignal(str)

    def __init__(self, options: list, default: str = None, parent=None):
        super().__init__(parent)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)
        self._btns = []
        for opt in options:
            b = QPushButton(opt)
            b.setObjectName("segBtn")
            b.setCheckable(True)
            b.clicked.connect(lambda _=False, bb=b, oo=opt: self._pick(bb, oo))
            lay.addWidget(b)
            self._btns.append(b)
        lay.addStretch()
        self.set_current(default or (options[0] if options else ""))

    def _pick(self, btn, opt):
        for b in self._btns:
            b.setChecked(b is btn)
        self.selected.emit(opt)

    def set_current(self, opt: str):
        for b in self._btns:
            b.setChecked(b.text() == opt)

    def current(self) -> str:
        for b in self._btns:
            if b.isChecked():
                return b.text()
        return ""


# ---------- 表单行助手 ----------
def form_row(label: str, widget, label_w: int = 100) -> QHBoxLayout:
    """label + 控件/布局 的水平表单行（设计稿左栏风格）"""
    from PyQt5.QtWidgets import QLayout
    row = QHBoxLayout()
    row.setSpacing(8)
    lbl = QLabel(label)
    lbl.setStyleSheet("color:#cbd5e1; font-size:16px; background:transparent;")
    lbl.setFixedWidth(label_w)
    row.addWidget(lbl)
    if isinstance(widget, QLayout):
        row.addLayout(widget, 1)
    elif widget.maximumWidth() < 100000:  # 固定小控件（Toggle 等）
        row.addWidget(widget)
        row.addStretch(1)
    else:
        row.addWidget(widget, 1)
    return row


# ---------- 区块标题（对齐参考版设计规范） ----------
class SectionTitle(QLabel):
    """左侧 3px 彩色竖条 + 16px 加粗标题（卡片内区块标题）"""

    def __init__(self, text: str = "", color: str = "#2563eb", parent=None):
        super().__init__(text, parent)
        self.setStyleSheet(
            f"border-left: 3px solid {color}; padding-left: 8px;"
            "font-size: 16px; font-weight: 600; color: #f1f5f9;"
            "background: transparent; border-top: none; border-right: none; border-bottom: none;")
        self.setFixedHeight(28)


# ---------- 页面头部（对齐参考版设计规范） ----------
class PageHeader(QWidget):
    """页面标题 + 可选副标题，固定高 48px"""

    def __init__(self, title: str, subtitle: str = "", parent=None):
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 2, 0, 2)
        lay.setSpacing(2)
        self.title = QLabel(title)
        self.title.setStyleSheet(
            "font-size: 20px; font-weight: 700; color: #f1f5f9; background: transparent;")
        lay.addWidget(self.title)
        if subtitle:
            self.subtitle = QLabel(subtitle)
            self.subtitle.setStyleSheet(
                "font-size: 15px; color: #64748b; background: transparent;")
            lay.addWidget(self.subtitle)
        self.setFixedHeight(48 if subtitle else 34)


# ---------- 统一反馈（对齐参考版设计规范） ----------
class Feedback:
    """统一反馈入口：status 走状态栏 / warn / error 弹窗"""

    @staticmethod
    def _app() -> QWidget:
        from PyQt5.QtWidgets import QApplication
        w = QApplication.activeWindow()
        return w

    @staticmethod
    def status(text: str, timeout: int = 5000):
        w = Feedback._app()
        if w is not None and w.statusBar() is not None:
            w.statusBar().showMessage(text, timeout)

    @staticmethod
    def warn(text: str, title: str = "提示"):
        from PyQt5.QtWidgets import QMessageBox
        QMessageBox.warning(Feedback._app(), title, text)

    @staticmethod
    def error(text: str, title: str = "错误"):
        from PyQt5.QtWidgets import QMessageBox
        QMessageBox.critical(Feedback._app(), title, text)

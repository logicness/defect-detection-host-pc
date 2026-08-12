"""
P3 历史记录页（设计稿图 3）
顶部：查询条件单行（时间/产品/结果分段/缺陷类型/路径关键词/查询/重置/导出）
KPI：检测总数 / OK数量 / NG数量 / 良率
左：检测记录表 + 分页；右：记录详情（预览图 + 字段 + 查看原图/打开目录）
"""
import os
import subprocess

from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QComboBox,
    QLineEdit, QFileDialog, QHeaderView, QMessageBox, QApplication
)
from PyQt5.QtCore import Qt, pyqtSignal, QDateTime
from PyQt5.QtGui import QImage

from components.common_widgets import Card, KPICard, SegGroup, StyledTable, form_row, FocusComboBox, DateTimePicker
from components.image_preview import ImagePreview
from components.stats_charts import TrendChart, DefectPieChart


def _lbl(text):
    l = QLabel(text)
    l.setStyleSheet("color:#cbd5e1; font-size:16px; background:transparent;")
    return l


def _popup(parent, title: str, text: str):
    """信息弹窗（offscreen 无头环境跳过，避免崩溃）"""
    if QApplication.platformName() != "offscreen":
        QMessageBox.information(parent, title, text)


class HistoryRecordPage(QWidget):
    query_requested = pyqtSignal(dict, int, int)   # (filters, limit, offset)
    export_requested = pyqtSignal(dict)
    clear_history_requested = pyqtSignal()         # 请求清空所有检测记录

    def __init__(self, parent=None):
        super().__init__(parent)
        self._page = 1
        self._page_size = 15
        self._total = 0
        self._rows = []
        self._build()

    def _build(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(10, 10, 10, 10)
        root.setSpacing(10)

        root.addWidget(self._build_filter())
        root.addLayout(self._build_kpi())
        root.addWidget(self._build_charts())

        body = QHBoxLayout()
        body.setSpacing(10)
        body.addLayout(self._build_table(), 7)
        body.addLayout(self._build_detail(), 5)
        root.addLayout(body, 1)

    # ---------- 统计图表（A4：趋势折线 + 缺陷饼图） ----------
    def _build_charts(self):
        card = Card("统计图表")
        card.setMinimumHeight(150)
        card.setMaximumHeight(190)
        row = QHBoxLayout()
        row.setSpacing(10)
        self.trend_chart = TrendChart()
        self.pie_chart = DefectPieChart()
        self.trend_chart.setMinimumHeight(130)
        self.pie_chart.setMinimumHeight(130)
        row.addWidget(self.trend_chart, 3)
        row.addWidget(self.pie_chart, 2)
        card.body.addLayout(row)
        return card

    def update_charts(self, daily: list, types: list):
        """外部调用（main.py 查询回调）：刷新趋势图与饼图"""
        self.trend_chart.update_data(daily)
        self.pie_chart.update_data(types)

    # ---------- 查询条件 ----------
    def _build_filter(self):
        card = Card("查询条件")
        row = QHBoxLayout()
        row.setSpacing(8)
        self.dt_from = DateTimePicker()
        self.dt_from.setDateTime(QDateTime.currentDateTime().addDays(-1))
        self.dt_to = DateTimePicker()
        self.dt_to.setDateTime(QDateTime.currentDateTime())
        self.combo_product = FocusComboBox()
        self.combo_product.addItems(["全部", "Product_A_v1"])
        self.seg_result = SegGroup(["全部", "OK", "NG"])
        self.combo_defect = FocusComboBox()
        self.combo_defect.addItems(["全部"])
        self.edit_keyword = QLineEdit()
        self.edit_keyword.setPlaceholderText("请输入关键词")
        btn_q = QPushButton("⌕ 查询")
        btn_q.setObjectName("btnPrimary")
        btn_q.clicked.connect(lambda: self._query(1))
        btn_r = QPushButton("↺ 重置")
        btn_r.clicked.connect(self._reset)
        btn_e = QPushButton("⤓ 导出记录")
        btn_e.clicked.connect(lambda: self.export_requested.emit(self.get_filters()))
        btn_c = QPushButton("🗑 清空记录")
        btn_c.setToolTip("清空所有检测记录（不可恢复，请先导出备份）")
        btn_c.clicked.connect(self._on_clear_history)
        row.addWidget(_lbl("开始时间"))
        row.addWidget(self.dt_from)
        row.addWidget(_lbl("结束时间"))
        row.addWidget(self.dt_to)
        row.addWidget(_lbl("产品型号"))
        row.addWidget(self.combo_product)
        row.addWidget(_lbl("检测结果"))
        row.addWidget(self.seg_result)
        row.addWidget(_lbl("缺陷类型"))
        row.addWidget(self.combo_defect)
        row.addWidget(_lbl("图像路径"))
        row.addWidget(self.edit_keyword, 1)
        row.addWidget(btn_q)
        row.addWidget(btn_r)
        row.addWidget(btn_e)
        row.addWidget(btn_c)
        card.body.addLayout(row)
        return card

    # ---------- KPI ----------
    def _build_kpi(self):
        row = QHBoxLayout()
        row.setSpacing(10)
        self.kpi_total = KPICard("检测总数", 0)
        self.kpi_ok = KPICard("OK数量", 0, "#22c55e")
        self.kpi_ng = KPICard("NG数量", 0, "#ef4444")
        self.kpi_yield = KPICard("良率", "--")
        for k in (self.kpi_total, self.kpi_ok, self.kpi_ng, self.kpi_yield):
            row.addWidget(k)
        return row

    # ---------- 记录表 + 分页 ----------
    def _build_table(self):
        col = QVBoxLayout()
        card = Card("检测记录")
        self.table = StyledTable(
            ["序号", "时间", "产品", "结果", "缺陷类型", "面积 (px)", "置信度", "图像路径"])
        self.table.itemSelectionChanged.connect(self._on_select)
        card.body.addWidget(self.table, 1)

        pag = QHBoxLayout()
        pag.setSpacing(4)
        self.btn_first = QPushButton("首页")
        self.btn_prev = QPushButton("上页")
        for b in (self.btn_first, self.btn_prev):
            b.setObjectName("iconBtn")
            b.setFixedSize(48, 34)
        self.btn_first.clicked.connect(lambda: self._query(1))
        self.btn_prev.clicked.connect(lambda: self._query(self._page - 1))
        self._page_btns = []
        self._page_box = QHBoxLayout()
        self._page_box.setSpacing(4)
        pag.addWidget(self.btn_first)
        pag.addWidget(self.btn_prev)
        pag.addLayout(self._page_box)
        self.btn_next = QPushButton("下页")
        self.btn_last = QPushButton("末页")
        for b in (self.btn_next, self.btn_last):
            b.setObjectName("iconBtn")
            b.setFixedSize(48, 34)
        self.btn_next.clicked.connect(lambda: self._query(self._page + 1))
        self.btn_last.clicked.connect(
            lambda: self._query(max(1, (self._total - 1) // self._page_size + 1)))
        pag.addWidget(self.btn_next)
        pag.addWidget(self.btn_last)
        pag.addSpacing(12)
        self.combo_size = FocusComboBox()
        self.combo_size.addItems(["15 条/页", "30 条/页", "50 条/页"])
        self.combo_size.currentIndexChanged.connect(self._on_size)
        pag.addWidget(self.combo_size)
        pag.addStretch()
        self.lbl_total = QLabel("共 0 条")
        self.lbl_total.setStyleSheet("color:#94a3b8; background:transparent;")
        pag.addWidget(self.lbl_total)
        card.body.addLayout(pag)
        col.addWidget(card, 1)
        return col

    # ---------- 详情 ----------
    def _build_detail(self):
        col = QVBoxLayout()
        card = Card("记录详情")
        self.preview = ImagePreview()
        self.preview.setMaximumHeight(260)
        card.body.addWidget(self.preview)
        self.detail_table = StyledTable(["项目", "值"])
        self.detail_table.horizontalHeader().setVisible(False)
        self.detail_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Fixed)
        self.detail_table.setColumnWidth(0, 110)
        self._drows = {}
        for key in ("产品", "结果", "缺陷类型", "面积 (px)", "置信度", "检测时间", "图像路径"):
            row = self.detail_table.rowCount()
            self.detail_table.insertRow(row)
            from PyQt5.QtWidgets import QTableWidgetItem
            from PyQt5.QtGui import QColor
            k = QTableWidgetItem(key)
            k.setForeground(QColor("#94a3b8"))
            self.detail_table.setItem(row, 0, k)
            v = QTableWidgetItem("--")
            self.detail_table.setItem(row, 1, v)
            self._drows[key] = v
        card.body.addWidget(self.detail_table, 1)
        brow = QHBoxLayout()
        btn_view = QPushButton("查看原图")
        btn_view.setObjectName("btnPrimary")
        btn_view.clicked.connect(self._view_orig)
        btn_dir = QPushButton("打开目录")
        btn_dir.clicked.connect(self._open_dir)
        brow.addStretch()
        brow.addWidget(btn_view)
        brow.addSpacing(12)
        brow.addWidget(btn_dir)
        brow.addStretch()
        card.body.addLayout(brow)
        col.addWidget(card, 1)
        return col

    # ================= 数据 =================
    def set_defect_types(self, types: list):
        cur = self.combo_defect.currentText()
        self.combo_defect.clear()
        self.combo_defect.addItems(["全部"] + types)
        if cur in types:
            self.combo_defect.setCurrentText(cur)

    def get_filters(self) -> dict:
        return {
            "date_from": self.dt_from.dateTime().toString("yyyy-MM-dd HH:mm:ss"),
            "date_to": self.dt_to.dateTime().toString("yyyy-MM-dd HH:mm:ss"),
            "product": self.combo_product.currentText(),
            "result": self.seg_result.current(),
            "defect_type": self.combo_defect.currentText(),
            "keyword": self.edit_keyword.text().strip(),
        }

    def render(self, rows: list, total: int, kpi: dict):
        """main 查询后回填：rows 为当前页记录"""
        self._rows = rows
        self._total = total
        self.lbl_total.setText(f"共 {total} 条")
        self.kpi_total.set_value(kpi.get("total", 0))
        self.kpi_ok.set_value(kpi.get("ok", 0))
        self.kpi_ng.set_value(kpi.get("ng", 0))
        self.kpi_yield.set_value(f"{kpi.get('yield', 0):.1f}%")
        self.table.clear_rows()
        from PyQt5.QtGui import QColor
        base = (self._page - 1) * self._page_size
        for i, r in enumerate(rows):
            res = "NG" if r["result"] == "缺陷" else "OK"
            colors = {3: "#ef4444" if res == "NG" else "#22c55e"}
            self.table.add_row([
                base + i + 1, r["timestamp"], r["product"], res,
                r["defect_type"] if res == "NG" else "-",
                r["area"] if res == "NG" else "-",
                f"{r['confidence']:.2f}", r["image_path"] or "-"], colors)
        self._render_page_btns()

    def _render_page_btns(self):
        while self._page_box.count():
            item = self._page_box.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self._page_btns = []
        pages = max(1, (self._total + self._page_size - 1) // self._page_size)
        self._page = min(max(1, self._page), pages)
        nums = self._page_nums(pages)
        last = 0
        for n in nums:
            if n - last > 1:
                dots = QLabel("…")
                dots.setStyleSheet("color:#64748b; background:transparent;")
                self._page_box.addWidget(dots)
            b = QPushButton(str(n))
            b.setObjectName("iconBtn")
            b.setFixedSize(30, 28)
            if n == self._page:
                b.setStyleSheet(
                    "background-color:#2563eb; color:#ffffff; border:1px solid #2563eb;")
            b.clicked.connect(lambda _=False, nn=n: self._query(nn))
            self._page_box.addWidget(b)
            last = n

    def _page_nums(self, pages: int) -> list:
        """页码窗口：1..5 + 末页"""
        nums = list(range(1, min(pages, 5) + 1))
        if pages > 5:
            nums += [pages]
        if self._page not in nums and pages > 5:
            nums = sorted(set(nums + [self._page]))
        return nums

    # ================= 事件 =================
    def _query(self, page: int):
        self._page = max(1, page)
        self.query_requested.emit(
            self.get_filters(), self._page_size, (self._page - 1) * self._page_size)

    def refresh(self):
        self._query(self._page)

    def _on_size(self, idx):
        self._page_size = int(self.combo_size.currentText().split()[0])
        self._query(1)

    def _reset(self):
        self.dt_from.setDateTime(QDateTime.currentDateTime().addDays(-1))
        self.dt_to.setDateTime(QDateTime.currentDateTime())
        self.combo_product.setCurrentIndex(0)
        self.seg_result.set_current("全部")
        self.combo_defect.setCurrentIndex(0)
        self.edit_keyword.clear()
        self._query(1)

    def _on_clear_history(self):
        """点击清空记录：弹窗确认后通知主程序清空数据库"""
        if QApplication.platformName() == "offscreen":
            return
        reply = QMessageBox.question(
            self, "清空确认",
            "确定要清空所有检测记录吗？\n此操作不可恢复，建议先导出备份。",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if reply == QMessageBox.Yes:
            self.clear_history_requested.emit()

    def _on_select(self):
        rows = self.table.selectedItems()
        if not rows:
            return
        r = self._rows[rows[0].row()] if rows[0].row() < len(self._rows) else None
        if not r:
            return
        res = "NG" if r["result"] == "缺陷" else "OK"
        from PyQt5.QtGui import QColor
        vals = {"产品": r["product"], "结果": res,
                "缺陷类型": r["defect_type"] if res == "NG" else "-",
                "面积 (px)": r["area"] if res == "NG" else "-",
                "置信度": f"{r['confidence']:.2f}", "检测时间": r["timestamp"],
                "图像路径": r["image_path"] or "-"}
        for k, v in vals.items():
            item = self._drows[k]
            item.setText(str(v))
            if k == "结果":
                item.setForeground(QColor("#ef4444") if v == "NG" else QColor("#22c55e"))
        # 预览图：有原图则加载，否则占位
        path = r.get("image_path", "")
        if path and os.path.exists(path):
            img = QImage(path)
            if not img.isNull():
                self.preview.set_image(img)
                self.preview.set_detections(
                    [(r["defect_type"], r["confidence"], *map(float, r["bbox"].split(",")))]
                    if res == "NG" and r.get("bbox") else [])
                return
        self.preview.set_image(QImage())
        self.preview.set_detections([])

    def _view_orig(self):
        path = self._drows["图像路径"].text()
        if not path or path == "-":
            _popup(self, "无图像", "当前记录没有关联的图像文件。")
            return
        if os.path.exists(path):
            os.startfile(path)
        else:
            _popup(self, "图像不存在",
                   f"原图文件不存在或已被移动：\n{path}")

    def _open_dir(self):
        path = self._drows["图像路径"].text()
        if not path or path == "-":
            _popup(self, "无图像", "当前记录没有关联的图像文件。")
            return
        if os.path.exists(path):
            subprocess.run(["explorer", "/select,", os.path.normpath(path)])
        else:
            _popup(self, "图像不存在",
                   f"原图文件不存在或已被移动：\n{path}")

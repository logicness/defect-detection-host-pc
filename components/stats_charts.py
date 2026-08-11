"""
A4 历史统计图表组件
matplotlib 嵌入 PyQt5：缺陷趋势折线图 + 缺陷类型饼图
暗色主题与主界面一致
"""
import matplotlib
matplotlib.use("Qt5Agg")
import matplotlib.font_manager as fm

# 中文字体（Windows: Microsoft YaHei / SimHei；无则回退默认）
for _f in ["Microsoft YaHei", "SimHei", "SimSun"]:
    try:
        if any(_f in f.name for f in fm.fontManager.ttflist):
            matplotlib.rcParams["font.family"] = _f
            matplotlib.rcParams["axes.unicode_minus"] = False
            break
    except Exception:
        pass

from matplotlib.backends.backend_qt5agg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.figure import Figure
from PyQt5.QtWidgets import QWidget, QVBoxLayout, QLabel
from PyQt5.QtCore import Qt


class TrendChart(FigureCanvas):
    """缺陷趋势折线图（近 N 天 total/defect）"""

    def __init__(self, parent=None, width=5, height=2.6, dpi=100):
        self.fig = Figure(figsize=(width, height), dpi=dpi, facecolor="#111827")
        self.ax = self.fig.add_subplot(111)
        super().__init__(self.fig)
        self.setParent(parent)
        self._style()

    def _style(self):
        self.ax.set_facecolor("#111827")
        for spine in self.ax.spines.values():
            spine.set_color("#334155")
        self.ax.tick_params(colors="#94a3b8", labelsize=9)
        self.ax.xaxis.label.set_color("#94a3b8")
        self.ax.yaxis.label.set_color("#94a3b8")
        self.fig.tight_layout()

    def update_data(self, daily: list):
        """daily: [{"date","total","defect","pass"},...]"""
        self.ax.clear()
        self._style()
        if not daily:
            self.ax.text(0.5, 0.5, "暂无数据", ha="center", va="center",
                         color="#64748b", fontsize=13)
            self.draw()
            return
        dates = [d["date"][5:] for d in daily]  # MM-DD
        totals = [d["total"] for d in daily]
        defects = [d["defect"] for d in daily]
        self.ax.plot(dates, totals, marker="o", markersize=3,
                     color="#60a5fa", label="检测总数", linewidth=1.5)
        self.ax.plot(dates, defects, marker="s", markersize=3,
                     color="#ef4444", label="缺陷数", linewidth=1.5)
        self.ax.legend(loc="upper left", fontsize=9, facecolor="#1e293b",
                       edgecolor="#334155", labelcolor="#e2e8f0")
        self.ax.set_xlabel("日期", color="#94a3b8", fontsize=10)
        self.ax.set_ylabel("数量", color="#94a3b8", fontsize=10)
        self.ax.tick_params(axis="x", rotation=30)
        self.fig.tight_layout()
        self.draw()


class DefectPieChart(FigureCanvas):
    """缺陷类型饼图"""

    def __init__(self, parent=None, width=4, height=2.6, dpi=100):
        self.fig = Figure(figsize=(width, height), dpi=dpi, facecolor="#111827")
        self.ax = self.fig.add_subplot(111)
        super().__init__(self.fig)
        self.setParent(parent)
        self._style()

    def _style(self):
        self.ax.set_facecolor("#111827")

    def update_data(self, types: list):
        """types: [{"defect_type","count","avg_conf"},...]"""
        self.ax.clear()
        self._style()
        if not types:
            self.ax.text(0.5, 0.5, "暂无数据", ha="center", va="center",
                         color="#64748b", fontsize=13)
            self.draw()
            return
        labels = [t["defect_type"] for t in types]
        counts = [t["count"] for t in types]
        colors = ["#60a5fa", "#f59e0b", "#22c55e", "#a855f7",
                  "#ef4444", "#06b6d4", "#ec4899", "#84cc16"]
        # 合并极小项
        if len(counts) > 8:
            keep = counts[:7]
            keep_labels = labels[:7]
            keep.append(sum(counts[7:]))
            keep_labels.append("其他")
            counts, labels = keep, keep_labels
        wedges, _, autotexts = self.ax.pie(
            counts, labels=labels, autopct="%1.0f%%", startangle=90,
            colors=colors[:len(counts)], textprops={"color": "#e2e8f0", "fontsize": 9},
            wedgeprops={"edgecolor": "#111827", "linewidth": 1})
        for at in autotexts:
            at.set_color("#0f172a")
        self.fig.tight_layout()
        self.draw()


class StatsChartsPanel(QWidget):
    """统计图表面板：上=趋势折线，下=缺陷饼图"""

    def __init__(self, parent=None):
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(8)

        title = QLabel("生产统计")
        title.setStyleSheet("color:#e2e8f0; font-size:16px; font-weight:600;")
        lay.addWidget(title)

        self.trend = TrendChart()
        self.pie = DefectPieChart()
        lay.addWidget(self.trend, stretch=3)
        lay.addWidget(self.pie, stretch=2)

    def refresh(self, daily: list, types: list):
        self.trend.update_data(daily)
        self.pie.update_data(types)

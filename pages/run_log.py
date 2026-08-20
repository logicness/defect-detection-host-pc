"""
P5 运行日志页（设计稿图 5）
日志筛选（级别/模块分段 + 时间 + 关键词 + 查询/实时刷新/导出/清空）
实时日志表 | 日志详情 + 系统状态（运行时间/帧率/CPU/内存/磁盘）
底部：日志总数统计 + 当前时间
"""
import csv
import time
from collections import deque

import psutil
from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QLineEdit,
    QDateTimeEdit, QProgressBar, QFileDialog, QHeaderView, QMessageBox,
    QFrame, QSizePolicy
)
from PyQt5.QtCore import Qt, pyqtSignal, QTimer, QDateTime, QRectF
from PyQt5.QtGui import QColor, QPainter, QPainterPath, QLinearGradient, QPen, QFont

from components.common_widgets import Card, SegGroup, StyledTable, Toggle

_LEVEL_COLOR = {"INFO": "#22c55e", "WARN": "#f59e0b", "ERROR": "#ef4444"}
_LEVEL_CN = {"INFO": "信息", "WARN": "警告", "ERROR": "错误"}
_MODULE_CODE = {"系统": "SYS", "相机": "CAM", "模型": "MDL", "PLC": "PLC",
                "检测": "DET", "通信": "COM", "数据库": "DB"}


def _lbl(text):
    l = QLabel(text)
    l.setStyleSheet("color:#cbd5e1; font-size:16px; background:transparent;")
    return l


class MiniTrend(QWidget):
    """小型动态趋势图（GPU 使用率 / 温度 / 推理耗时）"""

    def __init__(self, title, color, maxv=100, parent=None):
        super().__init__(parent)
        self._title = title
        self._color = QColor(color)
        self._maxv = maxv
        self._values = deque(maxlen=60)
        self.setMinimumHeight(72)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        # 动态扫描线动画
        self._anim = QTimer(self)
        self._anim.timeout.connect(self.update)
        self._anim.start(80)

    def add_value(self, v):
        try:
            v = float(v)
        except Exception:
            return
        self._values.append(v)
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = self.rect().adjusted(4, 4, -4, -4)
        if r.width() <= 0 or r.height() <= 0:
            p.end()
            return

        # 背景
        p.setPen(Qt.NoPen)
        p.setBrush(QColor("#0f172a"))
        p.drawRoundedRect(r, 8, 8)

        # 网格线
        p.setPen(QPen(QColor("#1e293b"), 1))
        for i in range(1, 4):
            y = r.top() + r.height() * i / 4
            p.drawLine(r.left() + 2, int(y), r.right() - 2, int(y))

        # 数据曲线
        if self._values:
            n = len(self._values)
            step = r.width() / 59.0
            maxv = max(self._maxv, max(self._values) * 1.2)
            path = QPainterPath()
            for i, v in enumerate(self._values):
                x = r.left() + i * step
                y = r.bottom() - (min(v, maxv) / maxv) * r.height()
                if i == 0:
                    path.moveTo(x, y)
                else:
                    path.lineTo(x, y)

            # 渐变面积
            area = QPainterPath(path)
            area.lineTo(r.left() + (n - 1) * step, r.bottom())
            area.lineTo(r.left(), r.bottom())
            area.closeSubpath()
            grad = QLinearGradient(0, r.top(), 0, r.bottom())
            c = self._color
            grad.setColorAt(0, QColor(c.red(), c.green(), c.blue(), 90))
            grad.setColorAt(1, QColor(c.red(), c.green(), c.blue(), 10))
            p.fillPath(area, grad)

            # 曲线
            p.setPen(QPen(self._color, 2))
            p.drawPath(path)

            # 标题 / 最新值
            p.setFont(QFont("Microsoft YaHei", 9))
            p.setPen(QColor("#94a3b8"))
            p.drawText(r.adjusted(4, 2, -4, -4), Qt.AlignLeft | Qt.AlignTop, self._title)
            latest = self._values[-1]
            p.setPen(self._color)
            p.drawText(r.adjusted(4, 2, -4, -4), Qt.AlignRight | Qt.AlignTop, f"{latest:.0f}")

            # 动态扫描线（来回移动的发光竖线）
            scan_x = r.left() + ((time.time() * 60) % 1.0) * r.width()
            p.setPen(QPen(QColor(c.red(), c.green(), c.blue(), 60), 1))
            p.drawLine(int(scan_x), r.top() + 2, int(scan_x), r.bottom() - 2)

        p.end()


class RunLogPage(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        # 有界日志缓冲：长跑（24h 产线）防止内存无限增长
        from collections import deque
        self._logs = deque(maxlen=10000)   # (ts, level, module, msg, code)
        self._counts = {"INFO": 0, "WARN": 0, "ERROR": 0}
        self._code_seq = {}
        self._start = time.time()
        self._fps = 0.0
        self._build()
        self._timer = QTimer(self)   # 指定 parent，页面销毁时自动停止
        self._timer.timeout.connect(self._tick)
        self._timer.start(1000)

    def _build(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(10, 10, 10, 6)
        root.setSpacing(10)

        root.addWidget(self._build_filter())

        body = QHBoxLayout()
        body.setSpacing(10)
        body.addLayout(self._build_table(), 7)
        body.addLayout(self._build_right(), 5)
        root.addLayout(body, 1)
        self.btn_clear.clicked.connect(self.table.clear_rows)

        root.addLayout(self._build_bottom())

    # ---------- 筛选 ----------
    def _build_filter(self):
        card = Card("日志筛选")
        r1 = QHBoxLayout()
        r1.addWidget(_lbl("日志级别"))
        self.seg_level = SegGroup(["全部", "信息", "警告", "错误"])
        r1.addWidget(self.seg_level)
        r1.addSpacing(20)
        r1.addWidget(_lbl("模块"))
        self.seg_module = SegGroup(["全部", "系统", "相机", "模型", "PLC", "检测"])
        r1.addWidget(self.seg_module)
        r1.addStretch()
        card.body.addLayout(r1)

        r2 = QHBoxLayout()
        r2.addWidget(_lbl("开始时间"))
        self.dt_from = QDateTimeEdit()
        self.dt_from.setDisplayFormat("yyyy-MM-dd HH:mm:ss")
        self.dt_from.setDateTime(QDateTime.currentDateTime().addDays(-1))
        r2.addWidget(self.dt_from)
        r2.addWidget(_lbl("结束时间"))
        self.dt_to = QDateTimeEdit()
        self.dt_to.setDisplayFormat("yyyy-MM-dd HH:mm:ss")
        self.dt_to.setDateTime(QDateTime.currentDateTime())
        r2.addWidget(self.dt_to)
        r2.addWidget(_lbl("关键词"))
        self.edit_kw = QLineEdit()
        self.edit_kw.setPlaceholderText("请输入关键词")
        r2.addWidget(self.edit_kw, 1)
        btn_q = QPushButton("⌕ 查询")
        btn_q.setObjectName("btnPrimary")
        btn_q.clicked.connect(self._apply_filter)
        r2.addWidget(btn_q)
        r2.addWidget(_lbl("实时刷新"))
        self.tg_live = Toggle(True)
        r2.addWidget(self.tg_live)
        btn_e = QPushButton("导出日志")
        btn_e.clicked.connect(self._export)
        self.btn_clear = QPushButton("清空显示")
        r2.addWidget(btn_e)
        r2.addWidget(self.btn_clear)
        card.body.addLayout(r2)
        return card

    # ---------- 日志表 ----------
    def _build_table(self):
        col = QVBoxLayout()
        card = Card("实时日志")
        self.table = StyledTable(["时间", "级别", "模块", "内容"])
        self.table.horizontalHeader().setSectionResizeMode(3, QHeaderView.Stretch)
        self.table.itemSelectionChanged.connect(self._on_select)
        card.body.addWidget(self.table, 1)
        col.addWidget(card, 1)
        return col

    # ---------- 右栏 ----------
    def _build_right(self):
        col = QVBoxLayout()
        d = Card("日志详情")
        self.detail = {}
        grid = QVBoxLayout()
        for key in ("时间", "级别", "模块", "线程", "代码"):
            row = QHBoxLayout()
            k = QLabel(key)
            k.setFixedWidth(60)
            k.setStyleSheet("color:#94a3b8; background:transparent;")
            v = QLabel("--")
            v.setStyleSheet("color:#e2e8f0; background:transparent;")
            row.addWidget(k)
            row.addWidget(v, 1)
            grid.addLayout(row)
            self.detail[key] = v
        ck = QLabel("内容")
        ck.setStyleSheet("color:#94a3b8; background:transparent;")
        self.detail_content = QLabel("--")
        self.detail_content.setWordWrap(True)
        self.detail_content.setStyleSheet("color:#e2e8f0; background:transparent;")
        grid.addWidget(ck)
        grid.addWidget(self.detail_content)
        d.body.addLayout(grid)
        col.addWidget(d)

        s = Card("系统状态")
        # 本机运行时间 / 帧率
        self.lbl_uptime = QLabel("00:00:00")
        self.lbl_uptime.setStyleSheet("color:#e2e8f0; font-size:16px; background:transparent;")
        self.lbl_fps = QLabel("0 FPS")
        self.lbl_fps.setStyleSheet("color:#e2e8f0; font-size:16px; background:transparent;")
        r = QHBoxLayout()
        r.addWidget(_lbl("运行时间"))
        r.addWidget(self.lbl_uptime, 1)
        r.addWidget(_lbl("检测帧率"))
        r.addWidget(self.lbl_fps, 1)
        s.body.addLayout(r)
        # 本机 CPU/内存/磁盘
        self.bars = {}
        for name in ("CPU", "内存"):
            row = QHBoxLayout()
            row.addWidget(_lbl(name))
            bar = QProgressBar()
            bar.setRange(0, 100)
            bar.setTextVisible(False)
            bar.setFixedHeight(8)
            bar.setStyleSheet(
                "QProgressBar{background:#1e293b; border:none; border-radius:4px;}"
                "QProgressBar::chunk{background:#22c55e; border-radius:4px;}")
            row.addWidget(bar, 1)
            pct = QLabel("0%")
            pct.setFixedWidth(40)
            pct.setStyleSheet("color:#94a3b8; background:transparent;")
            row.addWidget(pct)
            s.body.addLayout(row)
            self.bars[name] = (bar, pct)
        row = QHBoxLayout()
        row.addWidget(_lbl("磁盘剩余"))
        self.lbl_disk = QLabel("--")
        self.lbl_disk.setStyleSheet("color:#e2e8f0; background:transparent;")
        row.addWidget(self.lbl_disk, 1)
        s.body.addLayout(row)
        # 分隔线
        sep = QFrame()
        sep.setFrameShape(QFrame.HLine)
        sep.setStyleSheet("color:#1e293b; background:#1e293b; max-height:1px; border:none;")
        s.body.addWidget(sep)
        # Nano GPU 监控
        self._init_nano_monitor(s)
        col.addWidget(s)
        return col

    # ---------- Nano GPU 监控 ----------
    def _init_nano_monitor(self, card):
        self.lbl_nano_state = QLabel("● 未连接")
        self.lbl_nano_state.setStyleSheet(
            "color:#ef4444; font-size:14px; background:transparent;")
        head = QHBoxLayout()
        head.addWidget(_lbl("Nano GPU 监控"))
        head.addStretch()
        head.addWidget(self.lbl_nano_state)
        card.body.addLayout(head)

        # Nano GPU / 内存进度条（样式与 PC 端一致）
        self.nano_bars = {}
        for name in ("GPU", "Nano内存"):
            row = QHBoxLayout()
            row.addWidget(_lbl(name))
            bar = QProgressBar()
            bar.setRange(0, 100)
            bar.setTextVisible(False)
            bar.setFixedHeight(8)
            bar.setStyleSheet(
                "QProgressBar{background:#1e293b; border:none; border-radius:4px;}"
                "QProgressBar::chunk{background:#22d3ee; border-radius:4px;}")
            row.addWidget(bar, 1)
            pct = QLabel("--")
            pct.setFixedWidth(56)
            pct.setStyleSheet("color:#94a3b8; background:transparent;")
            row.addWidget(pct)
            card.body.addLayout(row)
            self.nano_bars[name] = (bar, pct)
        self.lbl_nano_gpu = self.nano_bars["GPU"][1]
        self.lbl_nano_mem = self.nano_bars["Nano内存"][1]

        # 温度 / 推理耗时行
        self.lbl_nano_temp = QLabel("温度: --")
        self.lbl_nano_ms = QLabel("推理: --")
        for lb in (self.lbl_nano_temp, self.lbl_nano_ms):
            lb.setStyleSheet(
                "color:#e2e8f0; font-size:14px; background:transparent;")
        row = QHBoxLayout()
        row.addWidget(self.lbl_nano_temp)
        row.addStretch()
        row.addWidget(self.lbl_nano_ms)
        card.body.addLayout(row)

        # 三个趋势图
        self.chart_gpu = MiniTrend("GPU %", "#22d3ee", 100)
        self.chart_temp = MiniTrend("温度 °C", "#fb923c", 100)
        self.chart_ms = MiniTrend("推理 ms", "#a78bfa", 60)
        charts = QHBoxLayout()
        charts.setSpacing(6)
        charts.addWidget(self.chart_gpu, 1)
        charts.addWidget(self.chart_temp, 1)
        charts.addWidget(self.chart_ms, 1)
        card.body.addLayout(charts)

        # 当前模型
        self.lbl_nano_model = QLabel("模型: --")
        self.lbl_nano_model.setStyleSheet(
            "color:#94a3b8; font-size:13px; background:transparent;")
        card.body.addWidget(self.lbl_nano_model)

    # ---------- 底部 ----------
    def _build_bottom(self):
        row = QHBoxLayout()
        self.lbl_total = QLabel("日志总数 0")
        self.lbl_info = QLabel("信息 0")
        self.lbl_info.setStyleSheet("color:#22c55e; background:transparent;")
        self.lbl_warn = QLabel("警告 0")
        self.lbl_warn.setStyleSheet("color:#f59e0b; background:transparent;")
        self.lbl_err = QLabel("错误 0")
        self.lbl_err.setStyleSheet("color:#ef4444; background:transparent;")
        self.lbl_time = QLabel("")
        self.lbl_time.setStyleSheet("color:#94a3b8; background:transparent;")
        for w in (self.lbl_total,):
            w.setStyleSheet("color:#cbd5e1; background:transparent;")
        row.addWidget(self.lbl_total)
        row.addSpacing(24)
        row.addWidget(self.lbl_info)
        row.addSpacing(24)
        row.addWidget(self.lbl_warn)
        row.addSpacing(24)
        row.addWidget(self.lbl_err)
        row.addStretch()
        row.addWidget(self.lbl_time)
        return row

    # ================= 公共接口 =================
    def append_log(self, level: str, module: str, msg: str):
        ts = time.strftime("%Y-%m-%d %H:%M:%S")
        code_key = _MODULE_CODE.get(module, "SYS")
        self._code_seq[code_key] = self._code_seq.get(code_key, 0) + 1
        code = f"{code_key}-{self._code_seq[code_key]:04d}"
        self._logs.append((ts, level, module, msg, code))
        if level in self._counts:
            self._counts[level] += 1
        self._update_bottom()
        if self.tg_live.isChecked() and self._match(ts, level, module, msg):
            self._add_row(ts, level, module, msg)

    def set_fps(self, fps: float):
        self._fps = fps

    def update_nano_status(self, status: dict):
        """Nano status_response → 更新 GPU/内存/温度/推理耗时/模型"""
        self.lbl_nano_state.setText("● 在线")
        self.lbl_nano_state.setStyleSheet(
            "color:#22c55e; font-size:14px; background:transparent;")
        gpu = status.get("gpu_util")
        temp = status.get("gpu_temp")
        ms = status.get("last_detect_ms")
        mem_used = status.get("mem_used_gb")
        mem_total = status.get("mem_total_gb")
        model = status.get("model")
        if gpu is not None:
            self._set_nano_bar("GPU", float(gpu), f"{gpu:.0f}%")
            self.chart_gpu.add_value(gpu)
        if mem_used is not None and mem_total:
            pct = mem_used / mem_total * 100.0
            self._set_nano_bar("Nano内存", pct, f"{mem_used:.1f}/{mem_total:.1f}G")
        if temp is not None:
            self.lbl_nano_temp.setText(f"温度: {temp:.0f}°C")
            self.chart_temp.add_value(temp)
        if ms is not None:
            self.lbl_nano_ms.setText(f"推理: {ms:.1f}ms")
            self.chart_ms.add_value(ms)
        self.lbl_nano_model.setText(f"模型: {model}" if model else "模型: --")

    def set_nano_online(self, online: bool):
        """Nano 连接状态切换（连接/断开）"""
        if online:
            self.lbl_nano_state.setText("● 在线")
            self.lbl_nano_state.setStyleSheet(
                "color:#22c55e; font-size:14px; background:transparent;")
        else:
            self.lbl_nano_state.setText("● 未连接")
            self.lbl_nano_state.setStyleSheet(
                "color:#ef4444; font-size:14px; background:transparent;")
            for name, (bar, lbl) in self.nano_bars.items():
                bar.setValue(0)
                lbl.setText("--")
            self.lbl_nano_temp.setText("温度: --")
            self.lbl_nano_ms.setText("推理: --")

    # ================= 内部 =================
    def _match(self, ts, level, module, msg) -> bool:
        lv = self.seg_level.current()
        if lv != "全部" and _LEVEL_CN.get(level, "") != lv:
            return False
        if self.seg_module.current() != "全部" and self.seg_module.current() != module:
            return False
        kw = self.edit_kw.text().strip()
        if kw and kw not in msg:
            return False
        return True

    def _add_row(self, ts, level, module, msg):
        colors = {1: _LEVEL_COLOR.get(level, "#94a3b8")}
        self.table.add_row([ts, _LEVEL_CN.get(level, level), module, msg], colors)
        if self.table.rowCount() > 2000:
            self.table.removeRow(0)

    def _apply_filter(self):
        self.table.clear_rows()
        for ts, level, module, msg, _ in self._logs:
            if self._match(ts, level, module, msg):
                self._add_row(ts, level, module, msg)

    def _on_select(self):
        items = self.table.selectedItems()
        if not items:
            return
        row = items[0].row()
        ts = self.table.item(row, 0).text()
        # 找回原始记录
        for (lts, level, module, msg, code) in reversed(self._logs):
            if lts == ts:
                self.detail["时间"].setText(lts)
                lv = self.detail["级别"]
                lv.setText(_LEVEL_CN.get(level, level))
                lv.setStyleSheet(f"color:{_LEVEL_COLOR.get(level, '#e2e8f0')}; background:transparent;")
                self.detail["模块"].setText(module)
                self.detail["线程"].setText("MainThread")
                self.detail["代码"].setText(code)
                self.detail_content.setText(msg)
                break

    def _update_bottom(self):
        total = sum(self._counts.values())
        self.lbl_total.setText(f"日志总数 {total}")
        self.lbl_info.setText(f"信息 {self._counts['INFO']}")
        self.lbl_warn.setText(f"警告 {self._counts['WARN']}")
        self.lbl_err.setText(f"错误 {self._counts['ERROR']}")

    def _tick(self):
        self.lbl_time.setText(f"当前时间: {time.strftime('%Y-%m-%d %H:%M:%S')}")
        up = int(time.time() - self._start)
        self.lbl_uptime.setText(f"{up // 3600:02d}:{up % 3600 // 60:02d}:{up % 60:02d}")
        self.lbl_fps.setText(f"{self._fps:.0f} FPS")
        try:
            cpu = psutil.cpu_percent(interval=None)
            mem = psutil.virtual_memory().percent
            disk = psutil.disk_usage("D:/")
            self._set_bar("CPU", cpu)
            self._set_bar("内存", mem)
            self.lbl_disk.setText(f"{disk.free / (1024 ** 3):.0f} GB")
        except Exception:
            pass

    def _set_bar(self, name, pct):
        bar, lbl = self.bars[name]
        bar.setValue(int(pct))
        lbl.setText(f"{int(pct)}%")
        # 动态渐变颜色：低绿 / 中橙 / 高红
        if pct < 60:
            color = "#22c55e"
        elif pct < 85:
            color = "#f59e0b"
        else:
            color = "#ef4444"
        bar.setStyleSheet(
            f"QProgressBar{{background:#1e293b; border:none; border-radius:4px;}}"
            f"QProgressBar::chunk{{background:qlineargradient("
            f"x1:0,y1:0,x2:1,y2:0, stop:0 {color}, stop:1 #38bdf8); border-radius:4px;}}")

    def _set_nano_bar(self, name, pct, text):
        bar, lbl = self.nano_bars[name]
        bar.setValue(int(pct))
        lbl.setText(text)
        # GPU/内存进度条动态颜色（与 PC 端一致）
        if pct < 60:
            color = "#22c55e"
        elif pct < 85:
            color = "#f59e0b"
        else:
            color = "#ef4444"
        bar.setStyleSheet(
            f"QProgressBar{{background:#1e293b; border:none; border-radius:4px;}}"
            f"QProgressBar::chunk{{background:qlineargradient("
            f"x1:0,y1:0,x2:1,y2:0, stop:0 {color}, stop:1 #38bdf8); border-radius:4px;}}")

    def _export(self):
        path, _ = QFileDialog.getSaveFileName(self, "导出日志", "run_log.csv",
                                              "CSV (*.csv)")
        if not path:
            return
        with open(path, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.writer(f)
            w.writerow(["时间", "级别", "模块", "内容", "代码"])
            for ts, level, module, msg, code in self._logs:
                w.writerow([ts, _LEVEL_CN.get(level, level), module, msg, code])
        QMessageBox.information(
            self, "导出成功",
            f"已导出 {len(self._logs)} 条日志到：\n{path}")

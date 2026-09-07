"""
P4 通信设置页（设计稿图 4）
PLC通信(Modbus TCP) | I/O信号映射表
串口通信(真实收发)   | 通信测试(十六进制收发)
通信状态：TX/RX/ERR/RTT KPI + 收发日志
"""
from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QComboBox,
    QLineEdit, QCheckBox, QTextEdit, QSizePolicy, QMessageBox, QApplication
)
from PyQt5.QtCore import Qt, pyqtSignal

from components.common_widgets import Card, KPICard, StatusLight, StyledTable, form_row, SpinBox, DoubleSpinBox, FocusComboBox
from core.serial_client import SerialClient


def _lbl(text):
    l = QLabel(text)
    l.setStyleSheet("color:#cbd5e1; font-size:16px; background:transparent;")
    return l


def _style_input(w):
    """统一输入控件样式：36px 高、16px 字体"""
    w.setFixedHeight(36)
    w.setStyleSheet("font-size:16px;")
    return w


def _popup(parent, title: str, text: str):
    """信息弹窗（offscreen 无头环境跳过，避免崩溃）"""
    if QApplication.platformName() != "offscreen":
        QMessageBox.information(parent, title, text)


class CommSettingPage(QWidget):
    plc_connect_requested = pyqtSignal()
    plc_disconnect_requested = pyqtSignal()
    test_comm_requested = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.serial = SerialClient()
        self.serial.data_received.connect(self._on_serial_rx)
        self.serial.error_occurred.connect(
            lambda m: self.append_txrx(f"[ERR] {m}"))
        self._build()

    def _build(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(10, 10, 10, 10)
        root.setSpacing(10)

        top = QHBoxLayout()
        top.setSpacing(10)
        top.addLayout(self._build_plc(), 4)
        top.addLayout(self._build_io(), 7)
        root.addLayout(top, 5)

        mid = QHBoxLayout()
        mid.setSpacing(10)
        mid.addLayout(self._build_serial(), 4)
        mid.addLayout(self._build_test(), 7)
        root.addLayout(mid, 5)

        root.addWidget(self._build_status(), 3)

    # ---------- PLC 通信 ----------
    def _build_plc(self):
        col = QVBoxLayout()
        col.setSpacing(8)
        card = Card("PLC通信")
        card.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.combo_proto = FocusComboBox()
        self.combo_proto.addItems(["Modbus TCP"])
        self.edit_ip = QLineEdit("<PLC_LAN_IP>")
        self.spin_port = SpinBox()
        self.spin_port.setRange(1, 65535)
        self.spin_port.setValue(2000)
        self.spin_unit = SpinBox()
        self.spin_unit.setRange(0, 255)
        self.spin_unit.setValue(1)
        self.spin_timeout = SpinBox()
        self.spin_timeout.setRange(100, 60000)
        self.spin_timeout.setValue(3000)
        self.spin_poll = SpinBox()
        self.spin_poll.setRange(50, 10000)
        self.spin_poll.setValue(500)
        for lbl, w in (("通信协议", self.combo_proto), ("服务器IP", self.edit_ip),
                       ("端口号", self.spin_port), ("单元ID", self.spin_unit),
                       ("连接超时 (ms)", self.spin_timeout), ("心跳周期 (ms)", self.spin_poll)):
            _style_input(w)
            card.body.addLayout(form_row(lbl, w, 160))
        row = QHBoxLayout()
        row.setSpacing(8)
        self.light_plc = StatusLight("未连接")
        row.addWidget(self.light_plc)
        row.addStretch()
        self.btn_disc = QPushButton("断开连接")
        self.btn_disc.setFixedHeight(36)
        self.btn_disc.setStyleSheet("font-size:16px;")
        self.btn_disc.clicked.connect(self.plc_disconnect_requested)
        self.btn_test = QPushButton("测试通信")
        self.btn_test.setFixedHeight(36)
        self.btn_test.setStyleSheet("font-size:16px;")
        self.btn_test.setObjectName("btnPrimary")
        self.btn_test.clicked.connect(self.test_comm_requested)
        row.addWidget(self.btn_disc)
        row.addWidget(self.btn_test)
        card.body.addLayout(row)
        card.body.addStretch()
        col.addWidget(card, 1)
        return col

    # ---------- I/O 映射 ----------
    def _build_io(self):
        col = QVBoxLayout()
        card = Card("I/O信号映射")
        self.io_table = StyledTable(["信号名称", "方向", "地址", "数据类型", "当前值"])
        self._io_lights = {}
        io_rows = [
            ("检测触发", "输入", "0x0000", "BOOL"),
            ("检测完成", "输入", "0x0001", "BOOL"),
            ("OK结果", "输出", "0x0100", "BOOL"),
            ("NG结果", "输出", "0x0101", "BOOL"),
            ("设备故障", "输入", "0x0002", "BOOL"),
        ]
        for name, d, addr, dt in io_rows:
            row = self.io_table.rowCount()
            self.io_table.insertRow(row)
            from PyQt5.QtWidgets import QTableWidgetItem
            for c, v in enumerate((name, d, addr, dt)):
                it = QTableWidgetItem(v)
                it.setTextAlignment(Qt.AlignCenter)
                self.io_table.setItem(row, c, it)
            light = StatusLight("OFF")
            self.io_table.setCellWidget(row, 4, light)
            self._io_lights[name] = light
        card.body.addWidget(self.io_table, 1)
        col.addWidget(card, 1)
        return col

    # ---------- 串口 ----------
    def _build_serial(self):
        col = QVBoxLayout()
        col.setSpacing(8)
        card = Card("串口通信")
        card.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.combo_port = FocusComboBox()
        self.combo_port.addItems(SerialClient.list_ports() or ["COM1"])
        self.combo_baud = FocusComboBox()
        self.combo_baud.addItems(["115200", "9600", "19200", "38400", "57600"])
        self.combo_bits = FocusComboBox()
        self.combo_bits.addItems(["8", "7", "6"])
        self.combo_stop = FocusComboBox()
        self.combo_stop.addItems(["1", "1.5", "2"])
        self.combo_parity = FocusComboBox()
        self.combo_parity.addItems(["None", "Even", "Odd"])
        for lbl, w in (("串口", self.combo_port), ("波特率", self.combo_baud),
                       ("数据位", self.combo_bits), ("停止位", self.combo_stop),
                       ("校验位", self.combo_parity)):
            _style_input(w)
            card.body.addLayout(form_row(lbl, w, 160))
        row = QHBoxLayout()
        row.setSpacing(8)
        self.light_ser = StatusLight("未连接")
        row.addWidget(self.light_ser)
        row.addStretch()
        self.btn_ser_open = QPushButton("打开串口")
        self.btn_ser_open.setFixedHeight(36)
        self.btn_ser_open.setStyleSheet("font-size:16px;")
        self.btn_ser_open.setObjectName("btnPrimary")
        self.btn_ser_open.clicked.connect(self._toggle_serial)
        self.btn_ser_refresh = QPushButton("刷新串口")
        self.btn_ser_refresh.setFixedHeight(36)
        self.btn_ser_refresh.setStyleSheet("font-size:16px;")
        self.btn_ser_refresh.clicked.connect(self._refresh_ports)
        row.addWidget(self.btn_ser_open)
        row.addWidget(self.btn_ser_refresh)
        card.body.addLayout(row)
        card.body.addStretch()
        col.addWidget(card, 1)
        return col

    # ---------- 通信测试 ----------
    def _build_test(self):
        col = QVBoxLayout()
        card = Card("通信测试")
        row = QHBoxLayout()
        row.addWidget(_lbl("发送数据"))
        row.addStretch()
        self.chk_hex = QCheckBox("十六进制显示")
        self.chk_hex.setChecked(True)
        row.addWidget(self.chk_hex)
        card.body.addLayout(row)
        srow = QHBoxLayout()
        srow.setSpacing(8)
        self.edit_tx = QLineEdit("01 03 00 00 00 01")
        self.edit_tx.setFixedHeight(36)
        self.edit_tx.setStyleSheet("font-size:16px;")
        btn_send = QPushButton("发送")
        btn_send.setFixedHeight(36)
        btn_send.setStyleSheet("font-size:16px;")
        btn_send.setObjectName("btnPrimary")
        btn_send.clicked.connect(self._send_test)
        btn_clear = QPushButton("清空")
        btn_clear.setFixedHeight(36)
        btn_clear.setStyleSheet("font-size:16px;")
        btn_clear.clicked.connect(lambda: (self.edit_tx.clear(), self.txt_rx.clear()))
        srow.addWidget(self.edit_tx, 1)
        srow.addWidget(btn_send)
        srow.addWidget(btn_clear)
        card.body.addLayout(srow)
        card.body.addWidget(_lbl("接收数据"))
        self.txt_rx = QTextEdit()
        self.txt_rx.setReadOnly(True)
        card.body.addWidget(self.txt_rx, 1)
        col.addWidget(card, 1)
        return col

    # ---------- 通信状态 ----------
    def _build_status(self):
        card = Card("通信状态")
        row = QHBoxLayout()
        self.kpi_tx = KPICard("发送帧数", 0)
        self.kpi_rx = KPICard("接收帧数", 0)
        self.kpi_err = KPICard("错误帧", 0, "#ef4444")
        self.kpi_rtt = KPICard("平均延迟", "--", "#22c55e")
        for k in (self.kpi_tx, self.kpi_rx, self.kpi_err, self.kpi_rtt):
            row.addWidget(k)
        self.txt_log = QTextEdit()
        self.txt_log.setReadOnly(True)
        self.txt_log.setFixedWidth(520)
        row.addWidget(self.txt_log)
        card.body.addLayout(row)
        return card

    # ================= 公共接口 =================
    def get_plc_config(self) -> dict:
        return {"protocol": self.combo_proto.currentText(),
                "host": self.edit_ip.text().strip(),
                "port": self.spin_port.value(),
                "unit_id": self.spin_unit.value(),
                "timeout_ms": self.spin_timeout.value(),
                "poll_ms": self.spin_poll.value()}

    def apply_config(self, cfg: dict):
        p = cfg.get("plc", {})
        if p:
            self.edit_ip.setText(p.get("host", "<PLC_LAN_IP>"))
            self.spin_port.setValue(p.get("port", 2000))
            self.spin_unit.setValue(p.get("unit_id", 1))
            self.spin_timeout.setValue(p.get("timeout_ms", 3000))
            self.spin_poll.setValue(p.get("poll_ms", 500))

    def set_plc_conn_ui(self, on: bool):
        self.light_plc.set_status(1 if on else 0, "已连接" if on else "未连接")

    def update_io_state(self, running: bool, fault: bool, last_result: str = ""):
        """last_result: 'OK' / 'NG' / ''"""
        self._io_lights["检测完成"].set_status(1 if running else 0,
                                               "ON" if running else "OFF")
        self._io_lights["OK结果"].set_status(1 if last_result == "OK" else 0,
                                             "ON" if last_result == "OK" else "OFF")
        self._io_lights["NG结果"].set_status(2 if last_result == "NG" else 0,
                                             "ON" if last_result == "NG" else "OFF")
        self._io_lights["设备故障"].set_status(2 if fault else 0, "ON" if fault else "OFF")

    def update_stats(self, tx: int, rx: int, err: int, rtt: float):
        self.kpi_tx.set_value(tx)
        self.kpi_rx.set_value(rx)
        self.kpi_err.set_value(err)
        self.kpi_rtt.set_value(f"{rtt:.0f} ms")

    def append_txrx(self, line: str):
        import time
        import html as _html
        ts = time.strftime("%H:%M:%S")
        # 串口内容完全由设备/用户控制，转义防 HTML 注入
        self.txt_log.append(f'<span style="color:#64748b">{ts}</span> '
                            f'<span style="color:#cbd5e1">{_html.escape(str(line), quote=False)}</span>')
        self.txt_log.verticalScrollBar().setValue(
            self.txt_log.verticalScrollBar().maximum())

    # ================= 串口事件 =================
    def _refresh_ports(self):
        ports = SerialClient.list_ports()
        self.combo_port.clear()
        self.combo_port.addItems(ports or ["COM1"])

    def _toggle_serial(self):
        if self.serial.is_open:
            self.serial.close()
            self.light_ser.set_status(0, "未连接")
            self.btn_ser_open.setText("打开串口")
            _popup(self, "串口已关闭",
                   f"串口 {self.combo_port.currentText()} 已关闭。")
        else:
            parity_map = {"None": "N", "Even": "E", "Odd": "O"}
            ok = self.serial.open(
                self.combo_port.currentText(), int(self.combo_baud.currentText()),
                int(self.combo_bits.currentText()), float(self.combo_stop.currentText()),
                parity_map.get(self.combo_parity.currentText(), "N"))
            if ok:
                self.light_ser.set_status(1, "已连接")
                self.btn_ser_open.setText("关闭串口")
                _popup(self, "串口已打开",
                       f"串口 {self.combo_port.currentText()} "
                       f"@{self.combo_baud.currentText()} 打开成功。")
            else:
                _popup(self, "打开失败",
                       f"无法打开串口 {self.combo_port.currentText()}。\n\n"
                       "请检查：端口是否被占用、驱动是否正常、设备是否连接。")

    def _send_test(self):
        text = self.edit_tx.text().strip()
        if not text:
            _popup(self, "发送失败", "发送内容为空，请先输入要发送的数据。")
            return
        if not self.serial.is_open:
            _popup(self, "发送失败", "串口未打开，请先点击「打开串口」。")
            return
        if self.chk_hex.isChecked():
            ok = self.serial.send_hex(text)
            self.append_txrx(f"[TX] {text}") if ok else None
        else:
            ok = self.serial.send(text.encode("utf-8"))
            self.append_txrx(f"[TX] {text}") if ok else None
        if not ok:
            _popup(self, "发送失败", "串口写入失败，请检查连接后重试。")

    def _on_serial_rx(self, data: bytes):
        if self.chk_hex.isChecked():
            text = data.hex(" ").upper()
        else:
            text = data.decode("utf-8", errors="replace")
        self.txt_rx.append(text)
        self.append_txrx(f"[RX] {text}")

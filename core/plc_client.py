"""
PLC 通信客户端（Modbus TCP，纯 socket 实现，零第三方依赖）
功能码: 0x01 读线圈 / 0x03 读保持寄存器 / 0x05 写单线圈 / 0x0F 写多线圈 / 0x10 写多寄存器

默认地址映射（configure 可覆盖）:
  线圈: 0=产线运行 1=故障报警 2=剔除触发(写) 3=剔除完成确认
  寄存器: 0=合格计数 1=缺陷计数

后台线程轮询产线状态，Qt 信号回主线程；统计 TX/RX/ERR/RTT 供通信状态卡显示
"""
import socket
import struct
import threading
import time
from PyQt5.QtCore import QObject, pyqtSignal


class ModbusError(Exception):
    def __init__(self, code: int):
        msgs = {1: "非法功能码", 2: "非法数据地址", 3: "非法数据值", 4: "从站设备故障"}
        super().__init__(f"Modbus 异常(0x{code:02X}): {msgs.get(code, '未知')}")


class PLCClient(QObject):
    connected = pyqtSignal()
    disconnected = pyqtSignal()
    error_occurred = pyqtSignal(str)
    log_message = pyqtSignal(str, str)
    status_updated = pyqtSignal(dict)   # running/fault/pass_count/defect_count/reject_ack/rtt_ms
    reject_ack = pyqtSignal(bool)

    def __init__(self):
        super().__init__()
        self._sock = None
        self._lock = threading.Lock()
        self._running = False
        self._connected = False
        self._tx = self._rx = self._err = 0
        self._txid = 0

        self.host = "<PLC_LAN_IP>"
        self.port = 2000
        self.unit_id = 1
        self.timeout = 3.0
        self.poll_interval = 0.5
        self.max_retries = 3
        self.retry_delay = 3.0

        self.coil_running, self.coil_fault = 0, 1
        self.coil_reject, self.coil_reject_ack = 2, 3
        self.reg_pass, self.reg_defect = 0, 1

    @property
    def is_connected(self) -> bool:
        return self._connected

    @property
    def is_running(self) -> bool:
        return self._running

    def get_stats(self) -> dict:
        return {"tx": self._tx, "rx": self._rx, "err": self._err}

    def configure(self, host=None, port=None, unit_id=None,
                  timeout=None, poll_interval=None, max_retries=None):
        if host is not None:
            self.host = host
        if port is not None:
            self.port = int(port)
        if unit_id is not None:
            self.unit_id = int(unit_id)
        if timeout is not None:
            self.timeout = float(timeout)
        if poll_interval is not None:
            self.poll_interval = float(poll_interval)
        if max_retries is not None:
            self.max_retries = int(max_retries)

    # ---------------- 连接 ----------------
    def connect(self):
        if self._running:
            return
        self._running = True
        threading.Thread(target=self._loop, daemon=True).start()

    def disconnect(self):
        self._running = False
        with self._lock:
            if self._sock:
                try:
                    self._sock.close()
                except OSError:
                    pass
                self._sock = None
        self._connected = False
        self.disconnected.emit()

    # ---------------- 业务 ----------------
    def trigger_reject(self) -> bool:
        """写剔除触发线圈（ON），返回是否写入成功"""
        try:
            self._transact(0x05, struct.pack(">HH", self.coil_reject, 0xFF00))
            return True
        except Exception as e:
            self.error_occurred.emit(f"剔除触发失败: {e}")
            return False

    def read_status(self) -> dict:
        """读 4 线圈 + 2 寄存器，组装产线状态"""
        coils = self._read_coils(0, 4)
        regs = self._read_regs(0, 2)
        return {
            "running": bool(coils[self.coil_running]),
            "fault": bool(coils[self.coil_fault]),
            "reject_ack": bool(coils[self.coil_reject_ack]),
            "pass_count": regs[self.reg_pass],
            "defect_count": regs[self.reg_defect],
        }

    # ---------------- Modbus 原语 ----------------
    def _read_coils(self, addr, n):
        data = self._transact(0x01, struct.pack(">HH", addr, n))
        return self._bits(data[1:], n)

    def _read_regs(self, addr, n):
        data = self._transact(0x03, struct.pack(">HH", addr, n))
        return struct.unpack(f">{n}H", data[1:])

    def _bits(self, payload: bytes, n: int) -> list:
        # 防御：线圈数据不足时抛明确异常，避免 IndexError 静默错乱
        need = (n + 7) // 8
        if len(payload) < need:
            raise ValueError(f"线圈数据长度不足: {len(payload)} < {need}")
        out = []
        for i in range(n):
            out.append(bool((payload[i // 8] >> (i % 8)) & 1))
        return out

    def _transact(self, fc: int, pdu_data: bytes) -> bytes:
        """发送一帧并收响应，返回 PDU 数据部分（不含功能码）"""
        pdu = bytes([fc]) + pdu_data
        self._txid = (self._txid + 1) & 0xFFFF
        mbap = struct.pack(">HHHB", self._txid, 0, len(pdu) + 1, self.unit_id)
        t0 = time.time()
        with self._lock:
            if not self._sock:
                raise ConnectionError("PLC 未连接")
            try:
                self._sock.sendall(mbap + pdu)
                self._tx += 1
                hdr = self._recv_exact(7)
                if not hdr or len(hdr) < 7:
                    raise ConnectionError("响应头不完整")
                _, _, ln, _ = struct.unpack(">HHHB", hdr)
                # Modbus 规范：PDU 长度（含功能码）最多 254；ln-1 为 PDU 长度
                if ln < 2 or ln > 255:
                    raise ConnectionError(f"MBAP 长度非法: {ln}")
                body = self._recv_exact(ln - 1)
                if not body:
                    raise ConnectionError("响应 PDU 不完整")
                self._rx += 1
            except Exception as e:
                self._err += 1
                raise e
        rtt = (time.time() - t0) * 1000
        # 异常帧：0x80 | fc
        if body[0] & 0x80:
            raise ModbusError(body[1] if len(body) > 1 else 0)
        # 功能码校验：对端串扰/重放时防止错配响应当本次结果
        if body[0] != fc:
            raise ConnectionError(f"响应功能码不匹配: 期望 {fc}, 实际 {body[0]}")
        self._last_rtt = rtt
        return body[1:] if body else b""

    def _recv_exact(self, n: int) -> bytes:
        buf = bytearray()
        while len(buf) < n:
            chunk = self._sock.recv(n - len(buf))
            if not chunk:
                return None
            buf.extend(chunk)
        return bytes(buf)

    # ---------------- 后台轮询 ----------------
    def _loop(self):
        retry = 0
        last_ok_ts = 0.0  # 上次连接成功时间（「秒断」风暴防护）
        while self._running:
            if not self._connected:
                try:
                    self.log_message.emit("INFO", f"PLC 连接 {self.host}:{self.port}...")
                    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                    sock.settimeout(self.timeout)
                    sock.connect((self.host, self.port))
                    with self._lock:
                        self._sock = sock
                    self._connected = True
                    retry = 0
                    last_ok_ts = time.time()
                    self.connected.emit()
                    self.log_message.emit("INFO", "PLC 连接成功")
                except OSError as e:
                    retry += 1
                    if retry > self.max_retries:
                        self.log_message.emit("ERROR", f"PLC 连接失败: {e}")
                        self._running = False
                        return
                    time.sleep(self.retry_delay)
                    continue
            try:
                st = self.read_status()
                st["rtt_ms"] = round(getattr(self, "_last_rtt", 0), 1)
                self.status_updated.emit(st)
            except Exception as e:
                self._connected = False
                with self._lock:
                    if self._sock:
                        try:
                            self._sock.close()
                        except OSError:
                            pass
                        self._sock = None
                self.disconnected.emit()
                self.log_message.emit("WARN", f"PLC 通信异常: {e}，重连中...")
                # 通信失败一律指数退避（TCP 能连但协议不通时防止紧循环重连风暴）
                retry += 1
                delay = min(self.retry_delay * (2 ** min(retry, 4)), 30)
                time.sleep(delay)
                continue
            retry = 0
            time.sleep(self.poll_interval)

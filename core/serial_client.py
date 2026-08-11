"""
串口通信客户端（pyserial 真实收发）
后台读线程持续接收，data_received 信号回主线程；支持十六进制发送
"""
import threading
import time
from PyQt5.QtCore import QObject, pyqtSignal

try:
    import serial
    import serial.tools.list_ports
    HAS_SERIAL = True
except ImportError:
    HAS_SERIAL = False


class SerialClient(QObject):
    connected = pyqtSignal()
    disconnected = pyqtSignal()
    error_occurred = pyqtSignal(str)
    data_received = pyqtSignal(bytes)
    log_message = pyqtSignal(str, str)

    def __init__(self):
        super().__init__()
        self._ser = None
        self._thread = None
        self._running = False

    @property
    def is_open(self) -> bool:
        return self._ser is not None and self._ser.is_open

    @staticmethod
    def list_ports() -> list:
        if not HAS_SERIAL:
            return []
        return [p.device for p in serial.tools.list_ports.comports()]

    def open(self, port: str, baudrate: int = 115200, bytesize: int = 8,
             stopbits: float = 1, parity: str = "N") -> bool:
        if not HAS_SERIAL:
            self.error_occurred.emit("pyserial 未安装")
            return False
        self.close()
        try:
            sb = {1: serial.STOPBITS_ONE, 1.5: serial.STOPBITS_ONE_POINT_FIVE,
                  2: serial.STOPBITS_TWO}.get(float(stopbits), serial.STOPBITS_ONE)
            self._ser = serial.Serial(
                port=port, baudrate=int(baudrate),
                bytesize=int(bytesize), stopbits=sb, parity=parity, timeout=0.1)
            self._running = True
            self._thread = threading.Thread(target=self._read_loop, daemon=True)
            self._thread.start()
            self.connected.emit()
            self.log_message.emit("INFO", f"串口 {port} 已打开")
            return True
        except Exception as e:
            self.error_occurred.emit(f"打开串口失败: {e}")
            return False

    def close(self):
        self._running = False
        if self._ser:
            try:
                self._ser.close()
            except Exception:
                pass
            self._ser = None
        self.disconnected.emit()

    def send(self, data: bytes) -> bool:
        if not self.is_open:
            self.error_occurred.emit("串口未打开")
            return False
        try:
            self._ser.write(data)
            return True
        except Exception as e:
            self.error_occurred.emit(f"发送失败: {e}")
            return False

    def send_hex(self, hex_str: str) -> bool:
        try:
            data = bytes.fromhex(hex_str.replace(" ", ""))
        except ValueError:
            self.error_occurred.emit("十六进制格式错误")
            return False
        return self.send(data)

    def _read_loop(self):
        while self._running and self._ser and self._ser.is_open:
            try:
                n = self._ser.in_waiting
                if n:
                    self.data_received.emit(self._ser.read(n))
                else:
                    time.sleep(0.02)
            except Exception as e:
                self.error_occurred.emit(f"串口接收异常: {e}")
                break
        self._running = False

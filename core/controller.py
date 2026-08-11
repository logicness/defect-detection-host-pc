"""
主控制器：连接 TCP / PLC / 数据库 / 各页面
统一处理推理结果（TCP 回传 或 本地模拟）：KPI 统计 / 连续 NG 告警 /
PLC 自动剔除 / 异步写库 / 结果信号分发
"""
import queue
import threading
import time
from datetime import datetime

from PyQt5.QtCore import QObject, pyqtSignal, QTimer

from core.tcp_client import TCPClient
from core.database import DatabaseManager
from core.plc_client import PLCClient

# NEU-DET 类别名
NEU_CLASSES = [
    "crazing", "inclusion", "patches",
    "pitted_surface", "rolled-in_scale", "scratches",
]


class _DBWriter(threading.Thread):
    """数据库异步写入线程：入队即返回，串行落库不阻塞 UI"""

    def __init__(self, db):
        super().__init__(daemon=True, name="db-writer")
        self.db = db
        self._q = queue.Queue(maxsize=500)
        self._stop = threading.Event()
        self.start()

    def run(self):
        while not self._stop.is_set():
            try:
                task = self._q.get(timeout=0.5)
            except queue.Empty:
                continue
            if task is None:
                break
            kind, kwargs = task
            try:
                if kind == "record":
                    self.db.insert_record(**kwargs)
                elif kind == "stats":
                    self.db.update_stats(**kwargs)
            except Exception as e:
                try:
                    self.db.log_message.emit("ERROR", f"数据库写入失败: {e}")
                except Exception:
                    pass

    def put(self, kind: str, **kwargs):
        try:
            self._q.put_nowait((kind, kwargs))
        except queue.Full:
            try:
                self.db.log_message.emit("WARN", "数据库写入队列已满(500)，丢弃记录以保实时")
            except Exception:
                pass

    def close(self):
        self._stop.set()
        try:
            self._q.put(None)
        except Exception:
            pass


class AppController(QObject):
    status_changed = pyqtSignal(str, int)   # (camera/model/plc, 0/1/2)
    fps_updated = pyqtSignal(float)
    detection_result = pyqtSignal(dict)     # UI 格式结果（含 frame）
    log_message = pyqtSignal(str, str, str)  # (level, module, msg)
    ng_alarm = pyqtSignal(int)

    def __init__(self):
        super().__init__()
        self.tcp = TCPClient()
        self.db = DatabaseManager()
        self.plc = PLCClient()
        self._dbw = _DBWriter(self.db)

        self.plc_auto_reject = True
        self.plc_reject_enabled = True
        self.ng_alarm_threshold = 5
        self._consecutive_ng = 0

        self._total = self._defect = 0
        self._frame_count = 0
        self._fps = 0.0
        self.last_image_path = ""

        self._fps_timer = QTimer()
        self._fps_timer.timeout.connect(self._calc_fps)
        self._fps_timer.start(1000)

        # 信号接线
        self.tcp.connected.connect(
            lambda: (self.status_changed.emit("model", 1),
                     self.log_message.emit("INFO", "通信", "TCP 连接成功")))
        self.tcp.disconnected.connect(lambda: self.status_changed.emit("model", 0))
        self.tcp.result_received.connect(self._on_tcp_result)
        self.tcp.error_occurred.connect(
            lambda m: self.log_message.emit("ERROR", "推理", m))
        self.tcp.log_message.connect(
            lambda lv, m: self.log_message.emit(lv, "通信", m))
        self.db.log_message.connect(
            lambda lv, m: self.log_message.emit(lv, "数据库", m))

        self.plc.connected.connect(
            lambda: (self.status_changed.emit("plc", 1),
                     self.log_message.emit("INFO", "PLC", "PLC 连接成功")))
        self.plc.disconnected.connect(
            lambda: (self.status_changed.emit("plc", 0),
                     self.log_message.emit("WARN", "PLC", "PLC 连接断开")))
        self.plc.error_occurred.connect(
            lambda m: self.log_message.emit("ERROR", "PLC", m))
        self.plc.log_message.connect(
            lambda lv, m: self.log_message.emit(lv, "PLC", m))
        self.plc.status_updated.connect(self._on_plc_status)

    # ---------------- 配置/连接 ----------------
    def configure_tcp(self, host, port, heartbeat=5, retries=3, timeout=10):
        self.tcp.configure(host, port, heartbeat, retries, timeout)

    def connect_tcp(self):
        self.tcp.connect()

    def disconnect_tcp(self):
        self.tcp.disconnect()

    def configure_plc(self, **kw):
        self.plc.configure(**kw)

    def connect_plc(self):
        self.plc.connect()

    def disconnect_plc(self):
        self.plc.disconnect()

    def trigger_reject(self) -> bool:
        return self.plc.trigger_reject()

    # ---------------- 推理入口 ----------------
    def send_image(self, image_bytes: bytes, image_path: str = ""):
        self.last_image_path = image_path
        self.tcp.send_image(image_bytes)

    def ingest_result(self, dets: list, frame=None, image_path: str = ""):
        """本地/模拟推理结果入口（UI 格式 dets），与 TCP 回传共用处理管线"""
        self._process(dets, frame, image_path or self.last_image_path)

    # ---------------- 结果管线 ----------------
    def _on_tcp_result(self, result: dict):
        """TCP 回传：raw 格式转 UI 格式后进入管线"""
        ui = []
        for det in result.get("detections", []):
            box = det.get("box", [0, 0, 0, 0])
            cid = det.get("class_id", 0)
            cls = NEU_CLASSES[cid] if cid < len(NEU_CLASSES) else f"cls{cid}"
            ui.append((cls, det.get("confidence", 0), *box[:4]))
        self._process(ui, result.get("frame"), self.last_image_path)

    def _process(self, dets: list, frame, image_path: str):
        self._total += 1
        has_defect = len(dets) > 0
        if has_defect:
            self._defect += 1
            self._consecutive_ng += 1
            if self.ng_alarm_threshold > 0 and \
                    self._consecutive_ng % self.ng_alarm_threshold == 0:
                self.ng_alarm.emit(self._consecutive_ng)
            if self.plc_auto_reject and self.plc.is_connected and self.plc_reject_enabled:
                self.plc.trigger_reject()
        else:
            self._consecutive_ng = 0

        # 异步写库
        for det in dets:
            x1, y1, x2, y2 = det[2:6]
            self._dbw.put("record", defect_type=det[0], confidence=det[1],
                          bbox=(x1, y1, x2, y2), area=abs((x2 - x1) * (y2 - y1)),
                          image_path=image_path, result="缺陷")
        if not has_defect:
            self._dbw.put("record", defect_type="none", confidence=0,
                          result="合格", image_path=image_path)
        confs = [d[1] for d in dets]
        self._dbw.put("stats", date=datetime.now().strftime("%Y-%m-%d"),
                      defect_count=1 if has_defect else 0,
                      pass_count=0 if has_defect else 1,
                      avg_conf=sum(confs) / len(confs) if confs else 0,
                      avg_fps=self._fps)

        self._frame_count += 1
        self.detection_result.emit({"detections": dets, "frame": frame,
                                    "image_path": image_path})

    def _on_plc_status(self, status: dict):
        running = status.get("running", False)
        if running != self.plc_reject_enabled:
            self.plc_reject_enabled = running
            self.log_message.emit(
                "WARN" if not running else "INFO", "PLC",
                "产线未运行，自动剔除已暂停" if not running else "产线运行，自动剔除恢复")
        if status.get("fault") and not getattr(self, "_fault_rep", False):
            self._fault_rep = True
            self.log_message.emit("ERROR", "PLC", "PLC 故障报警！")
        elif not status.get("fault"):
            self._fault_rep = False

    def _calc_fps(self):
        self._fps = self._frame_count
        self._frame_count = 0
        self.fps_updated.emit(float(self._fps))

    def get_stats(self) -> dict:
        return {"total": self._total, "defect": self._defect,
                "pass": self._total - self._defect, "fps": self._fps}

    def close(self):
        self.tcp.disconnect()
        self.plc.disconnect()
        try:
            self._dbw.close()
        except Exception:
            pass
        self.db.close()

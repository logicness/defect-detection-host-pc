"""
TCP 通信客户端（与下位机 RK3568/Orin 推理服务通信）
协议：4 字节大端长度包头 + UTF-8 JSON 载荷
功能：心跳保活 + 看门狗（半开连接主动重连）+ 指数退避重连 + 发送图片/接收结果 + 参数下发 + 模型管理
"""
import base64
import hashlib
import json
import os
import socket
import struct
import threading
import time
import logging
from queue import Queue, Empty
from PyQt5.QtCore import QObject, pyqtSignal

logger = logging.getLogger(__name__)

CHUNK_SIZE = 1 * 1024 * 1024  # 模型分片上传：1MB/片


class TCPClient(QObject):
    connected = pyqtSignal()
    disconnected = pyqtSignal()
    error_occurred = pyqtSignal(str)
    result_received = pyqtSignal(dict)     # detect_response
    log_message = pyqtSignal(str, str)     # (level, msg)
    model_list_received = pyqtSignal(dict)
    model_load_received = pyqtSignal(dict)
    model_upload_received = pyqtSignal(dict)  # model_upload_response
    model_status_received = pyqtSignal(dict)  # model_status（编译进度推送）
    model_delete_received = pyqtSignal(dict)  # model_delete_response

    def __init__(self):
        super().__init__()
        self._sock = None
        self._running = False
        self._connected = False
        self._lock = threading.Lock()
        self._send_queue = Queue()
        self._recv_thread = None
        self._last_recv = 0.0

        self.host = "192.168.1.101"
        self.port = 8888
        self.heartbeat_interval = 5
        self.max_retries = 3
        self.timeout = 10

    @property
    def is_connected(self) -> bool:
        return self._connected

    def configure(self, host: str, port: int, heartbeat: int = 5,
                  retries: int = 3, timeout: int = 10):
        self.host, self.port = host, int(port)
        self.heartbeat_interval = heartbeat
        self.max_retries = retries
        self.timeout = timeout

    # ---------------- 连接管理 ----------------
    def connect(self):
        if self._connected:
            return
        if self._running and self._recv_thread is not None \
                and not self._recv_thread.is_alive():
            self._running = False  # 上轮重试已耗尽，允许重新拉起
        if self._running:
            return
        self._running = True
        self._recv_thread = threading.Thread(target=self._connect_loop, daemon=True)
        self._recv_thread.start()

    def disconnect(self):
        self._running = False
        self._connected = False
        if self._sock:
            try:
                self._sock.close()
            except OSError:
                pass
            self._sock = None
        while not self._send_queue.empty():
            try:
                self._send_queue.get_nowait()
            except Exception:
                break
        self.disconnected.emit()
        self.log_message.emit("INFO", "TCP 连接已断开")

    # ---------------- 发送 ----------------
    def send_image(self, image_bytes: bytes):
        """detect_request：base64 图片"""
        self._enqueue({"type": "detect_request",
                       "image_base64": base64.b64encode(image_bytes).decode("ascii"),
                       "timestamp": time.time()})

    def send_control(self, conf_thres: float = None, iou_thres: float = None,
                     rois: list = None):
        payload = {"type": "control"}
        if conf_thres is not None:
            payload["conf_thres"] = conf_thres
        if iou_thres is not None:
            payload["iou_thres"] = iou_thres
        if rois is not None:
            payload["rois"] = rois
        self._enqueue(payload)

    def request_model_list(self):
        self._enqueue({"type": "model_list_request"})

    def load_model(self, name: str):
        self._enqueue({"type": "model_load_request", "model": name})

    def delete_model(self, name: str):
        """删除下位机端模型（激活模型会被拒绝）"""
        self._enqueue({"type": "model_delete_request", "model": name})

    def upload_model(self, path: str, progress_cb=None):
        """分片上传本地模型到下位机（后台线程执行）。
        progress_cb(received_bytes, total_bytes) 在子线程回调。
        .onnx/.pt 上传后下位机自动编译，编译进度经 model_status_received 推送。"""
        threading.Thread(
            target=self._upload_worker, args=(path, progress_cb), daemon=True).start()

    def _upload_worker(self, path: str, progress_cb=None):
        try:
            filename = os.path.basename(path)
            size = os.path.getsize(path)
            if size <= 0:
                self.log_message.emit("ERROR", f"上传失败: 文件为空 {path}")
                return
            self._enqueue({"type": "model_upload_start",
                           "filename": filename, "size": size,
                           "chunk_size": CHUNK_SIZE})
            sha = hashlib.sha256()
            seq = 0
            with open(path, "rb") as f:
                while True:
                    chunk = f.read(CHUNK_SIZE)
                    if not chunk:
                        break
                    sha.update(chunk)
                    self._enqueue({
                        "type": "model_upload_chunk",
                        "filename": filename,
                        "seq": seq,
                        "data": base64.b64encode(chunk).decode("ascii"),
                    })
                    seq += 1
                    if progress_cb:
                        progress_cb(min(seq * CHUNK_SIZE, size), size)
                    time.sleep(0.01)  # 让出发送线程
            self._enqueue({"type": "model_upload_end",
                           "filename": filename, "checksum": sha.hexdigest()})
            if progress_cb:
                progress_cb(size, size)
            self.log_message.emit(
                "INFO", f"模型上传完成: {filename} ({size/1e6:.1f}MB, {seq} 片)")
        except Exception as e:
            self.log_message.emit("ERROR", f"模型上传失败: {e}")

    def _enqueue(self, payload: dict):
        self._send_queue.put(json.dumps(payload, ensure_ascii=False).encode("utf-8"))

    # ---------------- 内部线程 ----------------
    def _connect_loop(self):
        retry = 0
        last_ok_ts = 0.0  # 上次连接成功时间（用于检测「秒断」风暴）
        while self._running:
            try:
                self.log_message.emit("INFO", f"正在连接 {self.host}:{self.port}...")
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sock.settimeout(self.timeout)
                sock.connect((self.host, self.port))
                sock.settimeout(None)
                with self._lock:
                    self._sock = sock
                    self._connected = True
                retry = 0
                last_ok_ts = time.time()
                self._last_recv = time.time()
                self.connected.emit()
                self.log_message.emit("INFO", f"已连接 {self.host}:{self.port}")
                threading.Thread(target=self._heartbeat_loop, daemon=True).start()
                threading.Thread(target=self._send_loop, daemon=True).start()
                self._recv_loop()
            except (ConnectionRefusedError, socket.timeout, OSError) as e:
                retry += 1
                if retry <= self.max_retries:
                    delay = min(2 ** retry, 30)
                    self.log_message.emit(
                        "WARN", f"连接失败({e})，{delay}s 后重试 ({retry}/{self.max_retries})")
                    time.sleep(delay)
                else:
                    # 重试耗尽不退出：开发板可能后开机/重启，持续后台自动重连
                    self.log_message.emit(
                        "WARN", f"连接失败({e})，持续自动重连中...")
                    retry = 0
                    time.sleep(5)
            finally:
                with self._lock:
                    self._connected = False
                    if self._sock:
                        try:
                            self._sock.close()
                        except OSError:
                            pass
                        self._sock = None
                if self._running:
                    self.disconnected.emit()
                # 「秒断」防护：连接成功但存活不足 2 秒（对端立即断开），退避 1s，
                # 避免 connect→recv EOF→reconnect 快速风暴刷日志/打满 CPU
                if last_ok_ts and time.time() - last_ok_ts < 2.0 and self._running:
                    self.log_message.emit("WARN", "连接存活过短，退避 1s 后重连")
                    time.sleep(1.0)

    def _heartbeat_loop(self):
        """心跳 + 看门狗：3 个周期无任何接收 → 判定对端失联，主动重连"""
        while self._running and self._connected:
            time.sleep(self.heartbeat_interval)
            if not self._connected:
                break
            try:
                self._send_raw(json.dumps(
                    {"type": "heartbeat", "timestamp": time.time()}).encode("utf-8"))
                if time.time() - self._last_recv > self.heartbeat_interval * 3:
                    self.log_message.emit("WARN", "心跳看门狗: 3 周期无数据，判定对端失联，主动重连")
                    self._force_reconnect()
                    break
            except Exception as e:
                self.log_message.emit("WARN", f"心跳发送失败: {e}")
                break

    def _force_reconnect(self):
        with self._lock:
            self._connected = False
            if self._sock:
                try:
                    self._sock.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
                try:
                    self._sock.close()
                except OSError:
                    pass
                self._sock = None

    def _send_loop(self):
        while self._running and self._connected:
            try:
                data = self._send_queue.get(timeout=1)
                self._send_raw(data)
            except Empty:
                continue
            except Exception as e:
                self.log_message.emit("ERROR", f"发送失败: {e}")
                break

    def _send_raw(self, data: bytes):
        with self._lock:
            if not self._sock:
                raise ConnectionError("Socket not connected")
            self._sock.sendall(struct.pack(">I", len(data)) + data)

    def _recv_loop(self):
        while self._running and self._connected:
            try:
                header = self._recv_exact(4)
                if not header:
                    break
                length = struct.unpack(">I", header)[0]
                if length <= 0 or length > 50 * 1024 * 1024:
                    self.log_message.emit("ERROR", f"无效载荷长度: {length}")
                    break
                body = self._recv_exact(length)
                if not body:
                    break
                self._last_recv = time.time()
                payload = json.loads(body.decode("utf-8"))
                t = payload.get("type", "")
                if t == "heartbeat_ack":
                    pass
                elif t == "detect_response":
                    self.result_received.emit(payload)
                elif t == "model_list_response":
                    self.model_list_received.emit(payload)
                elif t == "model_load_response":
                    self.model_load_received.emit(payload)
                elif t == "model_upload_response":
                    self.model_upload_received.emit(payload)
                elif t == "model_status":
                    self.model_status_received.emit(payload)
                elif t == "model_delete_response":
                    self.model_delete_received.emit(payload)
                elif t == "error":
                    self.error_occurred.emit(payload.get("message", "未知错误"))
                else:
                    self.log_message.emit("DEBUG", f"收到未知类型消息: {t}")
            except json.JSONDecodeError as e:
                self.log_message.emit("ERROR", f"JSON 解析失败: {e}")
            except OSError as e:
                if self._running:
                    self.log_message.emit("WARN", f"连接断开: {e}")
                break

    @staticmethod
    def _recv_exact_sock(sock, n: int) -> bytes:
        buf = bytearray()
        while len(buf) < n:
            chunk = sock.recv(n - len(buf))
            if not chunk:
                return None
            buf.extend(chunk)
        return bytes(buf)

    def _recv_exact(self, n: int) -> bytes:
        with self._lock:
            sock = self._sock
        if not sock:
            return None
        try:
            return self._recv_exact_sock(sock, n)
        except OSError:
            return None

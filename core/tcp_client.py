"""
TCP 通信客户端（与下位机 RK3568/Orin 推理服务通信）
协议：4 字节大端长度包头 + UTF-8 JSON 载荷
功能：心跳保活 + 看门狗（半开连接主动重连）+ 指数退避重连 + 发送图片/接收结果 + 参数下发 + 模型管理

2026-08-20 重构（修复）：
- 持锁 sendall → 锁内只取 sock 引用，锁外发送（原实现发送缓冲写满时持锁无限阻塞，
  看门狗/重连等同一把锁 → 永久死锁、连接悬挂）
- socket 读写超时 = 心跳间隔（原 settimeout(None) 无限阻塞，半开连接永久卡死）
- epoch 代际机制：旧连接的心跳/发送/接收线程自动退出，杜绝重连后双线程
- 心跳/发送线程退出时主动关闭 socket（不再无人关连接）
- 发送队列有界 + 实时流丢帧策略（原无界队列上传模型时内存无上限）
- 宽异常捕获（UnicodeDecodeError / 非 dict 载荷不再导致线程静默死亡）
- 秒断指数退避（原无退避重连风暴）
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
from queue import Queue, Empty, Full
from PyQt5.QtCore import QObject, pyqtSignal

logger = logging.getLogger(__name__)

CHUNK_SIZE = 1 * 1024 * 1024  # 模型分片上传：1MB/片
SEND_QUEUE_MAX = 64           # 发送队列上限（实时流丢帧策略，内存有界）


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
    status_received = pyqtSignal(dict)        # status_response（Nano 系统状态）
    # 下位机图片检测（2026-08-20 新增）
    nano_image_dir_received = pyqtSignal(dict)        # nano_image_dir_response
    nano_images_received = pyqtSignal(dict)           # nano_images_response
    nano_image_received = pyqtSignal(dict)            # nano_image_response
    nano_detect_received = pyqtSignal(dict)           # nano_detect_response
    nano_batch_received = pyqtSignal(dict)            # nano_batch_response / stop_response
    nano_batch_progress_received = pyqtSignal(dict)   # nano_batch_progress
    nano_batch_item_received = pyqtSignal(dict)       # nano_batch_item
    nano_batch_done_received = pyqtSignal(dict)       # nano_batch_done
    # 产线模拟流（2026-08-25）
    stream_frame_received   = pyqtSignal(dict)   # stream_frame（下位机主动推送帧）
    stream_state_received   = pyqtSignal(dict)   # stream_state
    stream_control_received = pyqtSignal(dict)   # stream_control_response / subscribe_response

    def __init__(self):
        super().__init__()
        self._sock = None
        self._running = False
        self._connected = False
        self._lock = threading.Lock()
        self._send_queue = Queue(maxsize=SEND_QUEUE_MAX)
        self._recv_thread = None
        self._last_recv = 0.0
        self._epoch = 0  # 连接代际：每次连接递增，旧线程据此退出

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
        with self._lock:
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
        """detect_request：base64 图片（实时流高频，队列满时丢最旧帧）"""
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

    def request_status(self):
        """请求下位机系统状态（GPU/CPU/内存/温度/最近推理耗时），响应经 status_received"""
        self._enqueue({"type": "status_request"})

    # ---------- 下位机图片检测（2026-08-20 新增） ----------
    def request_nano_image_dir(self, action="get", dir=None):
        """查询/设置下位机图片文件夹，响应经 nano_image_dir_received"""
        payload = {"type": "nano_image_dir_request", "action": action}
        if dir:
            payload["dir"] = dir
        self._enqueue(payload)

    def request_nano_images(self, offset=0, limit=100, thumb=True, thumb_size=256):
        """请求下位机图片列表（含缩略图），响应经 nano_images_received"""
        self._enqueue({"type": "nano_images_request", "offset": offset,
                       "limit": limit, "thumb": thumb, "thumb_size": thumb_size})

    def request_nano_image(self, name, size=640):
        """请求单张预览大图（base64），响应经 nano_image_received"""
        self._enqueue({"type": "nano_image_request", "name": name, "size": size})

    def request_nano_detect(self, name, annotate=True, thumb_size=640):
        """单张检测：下位机本地图片 + 下位机当前激活模型，响应经 nano_detect_received"""
        self._enqueue({"type": "nano_detect_request", "name": name,
                       "annotate": annotate, "thumb_size": thumb_size})

    def start_nano_batch(self, names, rounds=1, annotate=True, thumb_size=640):
        """启动下位机批量检测（多次检测），进度/条目/完成经 nano_batch_*_received"""
        self._enqueue({"type": "nano_batch_request", "names": list(names),
                       "rounds": max(1, int(rounds)), "annotate": annotate,
                       "thumb_size": thumb_size})

    def stop_nano_batch(self, session_id=None):
        """中止下位机批量检测"""
        payload = {"type": "nano_batch_stop_request"}
        if session_id:
            payload["session_id"] = session_id
        self._enqueue(payload)

    # ---------- 产线模拟流（2026-08-25） ----------
    def subscribe_stream(self, subscribe=True):
        """订阅/退订产线检测流，响应经 stream_control_received"""
        self._enqueue({"type": "stream_subscribe_request", "subscribe": bool(subscribe)})

    def control_stream(self, action, fps=None):
        """控制产线模拟相机（start/stop/set_fps），响应经 stream_control_received"""
        payload = {"type": "stream_control_request", "action": action}
        if fps is not None:
            payload["fps"] = int(fps)
        self._enqueue(payload)

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
                           "chunk_size": CHUNK_SIZE}, block=True)
            sha = hashlib.sha256()
            seq = 0
            with open(path, "rb") as f:
                while True:
                    chunk = f.read(CHUNK_SIZE)
                    if not chunk:
                        break
                    sha.update(chunk)
                    # 上传用阻塞入队（背压）：链路慢时等待而不是把整个模型堆进内存
                    self._enqueue({
                        "type": "model_upload_chunk",
                        "filename": filename,
                        "seq": seq,
                        "data": base64.b64encode(chunk).decode("ascii"),
                    }, block=True)
                    seq += 1
                    if progress_cb:
                        progress_cb(min(seq * CHUNK_SIZE, size), size)
            self._enqueue({"type": "model_upload_end",
                           "filename": filename, "checksum": sha.hexdigest()}, block=True)
            if progress_cb:
                progress_cb(size, size)
            self.log_message.emit(
                "INFO", f"模型上传完成: {filename} ({size/1e6:.1f}MB, {seq} 片)")
        except Exception as e:
            self.log_message.emit("ERROR", f"模型上传失败: {e}")

    def _enqueue(self, payload: dict, block: bool = False):
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        if block:
            self._send_queue.put(data)
            return
        try:
            self._send_queue.put_nowait(data)
        except Full:
            # 队列满：丢弃最旧一帧（实时流丢帧策略），保证内存有界
            try:
                self._send_queue.get_nowait()
                self._send_queue.put_nowait(data)
            except Exception:
                pass
            self.log_message.emit("WARN", "发送队列已满，丢弃最旧消息（实时流丢帧）")

    # ---------------- 内部线程 ----------------
    def _new_epoch(self) -> int:
        with self._lock:
            self._epoch += 1
            return self._epoch

    def _connect_loop(self):
        retry = 0
        last_ok_ts = 0.0  # 上次连接成功时间（用于检测「秒断」风暴）
        while self._running:
            epoch = self._new_epoch()
            try:
                self.log_message.emit("INFO", f"正在连接 {self.host}:{self.port}...")
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sock.settimeout(self.timeout)
                sock.connect((self.host, self.port))
                # 读写超时 = 心跳间隔：半开连接/对端停止读取时能自动恢复，
                # 不再无限阻塞（原 settimeout(None) 会导致连接永久悬挂）
                sock.settimeout(self.heartbeat_interval)
                with self._lock:
                    self._sock = sock
                    self._connected = True
                    self._epoch = epoch
                retry = 0
                last_ok_ts = time.time()
                self._last_recv = time.time()
                self.connected.emit()
                self.log_message.emit("INFO", f"已连接 {self.host}:{self.port}")
                threading.Thread(target=self._heartbeat_loop, args=(epoch,), daemon=True).start()
                threading.Thread(target=self._send_loop, args=(epoch,), daemon=True).start()
                self._recv_loop(epoch)
            except (ConnectionRefusedError, socket.timeout, OSError) as e:
                retry += 1
                if retry <= self.max_retries:
                    delay = min(2 ** retry, 30)
                    self.log_message.emit(
                        "WARN", f"连接失败({e})，{delay}s 后重试 ({retry}/{self.max_retries})")
                    time.sleep(delay)
                else:
                    # 手动连接模式：重试耗尽后停止，不再持续后台自动重连
                    self.log_message.emit(
                        "WARN", f"连接失败({e})，请检查下位机或手动点击「重新连接」")
                    self._running = False
                    break
            finally:
                with self._lock:
                    self._connected = False
                    if self._sock:
                        try:
                            self._sock.close()
                        except OSError:
                            pass
                        self._sock = None
                if self._running and epoch == self._epoch:
                    self.disconnected.emit()
                # 秒断退避：连接成功但存活过短 → 指数退避，避免无退避重连风暴
                if self._running and last_ok_ts and time.time() - last_ok_ts < 2.0:
                    delay = min(2 ** max(retry, 1), 15)
                    self.log_message.emit("WARN", f"连接存活过短，{delay}s 后重试")
                    time.sleep(delay)

    def _heartbeat_loop(self, epoch):
        """心跳 + 看门狗：3 个周期无任何接收 → 判定对端失联，主动重连。
        线程退出（异常/代际过期）时主动关闭 socket，杜绝连接悬挂无人回收。"""
        try:
            while self._running and self._connected and epoch == self._epoch:
                time.sleep(self.heartbeat_interval)
                if not self._connected or epoch != self._epoch:
                    break
                try:
                    self._send_raw(json.dumps(
                        {"type": "heartbeat", "timestamp": time.time()}).encode("utf-8"))
                    if time.time() - self._last_recv > self.heartbeat_interval * 3:
                        self.log_message.emit(
                            "WARN", "心跳看门狗: 3 周期无数据，判定对端失联，主动重连")
                        self._force_reconnect()
                        break
                except Exception as e:
                    self.log_message.emit("WARN", f"心跳发送失败: {e}")
                    self._force_reconnect()
                    break
        finally:
            # 心跳线程退出即意味着连接异常或已换代：确保 socket 被关闭
            if epoch == self._epoch and self._connected:
                self._force_reconnect()

    def _force_reconnect(self):
        """不取锁关闭 socket：锁可能被（旧实现遗留的）持锁 sendall 占着，
        socket 句柄的 shutdown/close 本身是线程安全的。"""
        with self._lock:
            sock = self._sock
            self._connected = False
        if sock is not None:
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            try:
                sock.close()
            except OSError:
                pass

    def _send_loop(self, epoch):
        while self._running and self._connected and epoch == self._epoch:
            try:
                data = self._send_queue.get(timeout=1)
            except Empty:
                continue
            try:
                self._send_raw(data)
            except Exception as e:
                self.log_message.emit("ERROR", f"发送失败: {e}")
                self._force_reconnect()
                break

    def _send_raw(self, data: bytes):
        # 锁内只取 socket 引用，sendall 在锁外执行：
        # 发送缓冲写满时 sendall 会阻塞，若持锁则看门狗/重连永远等不到锁 → 死锁
        with self._lock:
            if not self._sock:
                raise ConnectionError("Socket not connected")
            sock = self._sock
        sock.sendall(struct.pack(">I", len(data)) + data)

    def _recv_loop(self, epoch):
        while self._running and self._connected and epoch == self._epoch:
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
                if not isinstance(payload, dict):
                    self.log_message.emit(
                        "WARN", f"收到非 dict 载荷: {type(payload).__name__}")
                    continue
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
                elif t == "status_response":
                    self.status_received.emit(payload)
                elif t == "nano_image_dir_response":
                    self.nano_image_dir_received.emit(payload)
                elif t == "nano_images_response":
                    self.nano_images_received.emit(payload)
                elif t == "nano_image_response":
                    self.nano_image_received.emit(payload)
                elif t == "nano_detect_response":
                    self.nano_detect_received.emit(payload)
                elif t == "nano_batch_response":
                    self.nano_batch_received.emit(payload)
                elif t == "nano_batch_stop_response":
                    self.nano_batch_received.emit(payload)
                elif t == "nano_batch_progress":
                    self.nano_batch_progress_received.emit(payload)
                elif t == "nano_batch_item":
                    self.nano_batch_item_received.emit(payload)
                elif t == "nano_batch_done":
                    self.nano_batch_done_received.emit(payload)
                elif t == "stream_subscribe_response":
                    self.stream_control_received.emit(payload)
                elif t == "stream_control_response":
                    self.stream_control_received.emit(payload)
                elif t == "stream_frame":
                    self.stream_frame_received.emit(payload)
                elif t == "stream_state":
                    self.stream_state_received.emit(payload)
                elif t == "error":
                    self.error_occurred.emit(payload.get("message", "未知错误"))
                else:
                    self.log_message.emit("DEBUG", f"收到未知类型消息: {t}")
            except socket.timeout:
                # 心跳间隔内无数据属正常（读超时 = heartbeat_interval）
                continue
            except (UnicodeDecodeError, json.JSONDecodeError,
                    AttributeError, ValueError) as e:
                # 坏载荷不影响帧同步（4 字节长度头已消费），记录后继续
                self.log_message.emit("WARN", f"载荷解析异常: {e}")
                continue
            except OSError as e:
                if self._running and epoch == self._epoch:
                    self.log_message.emit("WARN", f"连接断开: {e}")
                break
            except Exception as e:
                self.log_message.emit("WARN", f"接收异常: {e}")
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


def queue_full():
    """兼容 Queue.Full（直接引用避免导入名冲突）"""
    from queue import Full
    return Full

"""
实时检测引擎
后台线程从帧源取帧 → 推理（sim 本地模拟 / tcp 经回调发下位机）→ 信号回传
支持开始/停止/帧率上限，错误自动退避
"""
import time
import threading
from PyQt5.QtCore import QObject, pyqtSignal
import cv2  # noqa: F401

from core.frame_source import FrameSource, SimFrameSource, sim_detect


class StreamEngine(QObject):
    frame_ready = pyqtSignal(object)     # numpy BGR 原始帧（预览用）
    result_received = pyqtSignal(dict)   # sim 模式单帧结果（含 detections/frame）
    fps_updated = pyqtSignal(float)
    state_changed = pyqtSignal(bool)
    error_occurred = pyqtSignal(str)
    log_message = pyqtSignal(str, str)

    def __init__(self, frame_source: FrameSource = None,
                 infer_mode: str = "sim", max_fps: float = 15.0):
        super().__init__()
        self.frame_source = frame_source or SimFrameSource()
        self.infer_mode = infer_mode  # "sim" | "tcp" | "local"
        self.max_fps = max_fps
        self._frame_interval = 1.0 / max_fps if max_fps > 0 else 0
        self._infer_cb = None  # tcp 模式回调: cb(frame) -> None（结果异步经 controller 回传）
        self._local_session = None  # local 模式：onnx session
        self._local_conf = 0.25
        self._local_iou = 0.45
        self._running = False
        self._thread = None
        self._stop_evt = threading.Event()

    @property
    def is_running(self) -> bool:
        return self._running

    def set_frame_source(self, fs: FrameSource):
        self.frame_source = fs

    def set_infer_callback(self, cb):
        self._infer_cb = cb

    def set_local_model(self, model_path: str, conf: float = 0.25, iou: float = 0.45):
        """local 模式：加载本地 ONNX 模型（失败发错误信号，回退 sim）"""
        try:
            from core.local_infer import load_session
            self._local_session = load_session(model_path)
            self._local_conf = conf
            self._local_iou = iou
            self.log_message.emit("INFO", f"本地模型已加载: {model_path}")
        except Exception as e:
            self._local_session = None
            self.error_occurred.emit(f"本地模型加载失败: {e}")
            self.log_message.emit("ERROR", f"本地模型加载失败: {e}")

    def clear_local_model(self):
        self._local_session = None

    def start(self):
        if self._running:
            return
        if not self.frame_source.open():
            self.error_occurred.emit("帧源打开失败")
            return
        self._running = True
        self._stop_evt.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        self.state_changed.emit(True)
        self.log_message.emit("INFO", f"实时流已启动（{self.infer_mode} 推理）")

    def stop(self):
        if not self._running:
            return
        self._running = False
        self._stop_evt.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=3.0)
        self._thread = None
        self.frame_source.close()
        self.state_changed.emit(False)
        self.log_message.emit("INFO", "实时流已停止")

    def _run(self):
        count, last_t = 0, time.time()
        while self._running and not self._stop_evt.is_set():
            t0 = time.time()
            try:
                ok, frame = self.frame_source.read()
                if not ok or frame is None:
                    self.log_message.emit("WARN", "帧源读取结束")
                    break

                if self.infer_mode == "sim":
                    dets = sim_detect(frame, self.frame_source)
                    self.result_received.emit({"detections": dets, "frame": frame})
                elif self.infer_mode == "local" and self._local_session is not None:
                    from core.local_infer import infer_frame
                    dets = infer_frame(self._local_session, frame,
                                       self._local_conf, self._local_iou)
                    self.result_received.emit({"detections": dets, "frame": frame})
                elif self._infer_cb:
                    self._infer_cb(frame)  # tcp：结果异步经 controller 回传

                self.frame_ready.emit(frame)

                count += 1
                now = time.time()
                if now - last_t >= 1.0:
                    self.fps_updated.emit(round(count / (now - last_t), 1))
                    count, last_t = 0, now
            except Exception as e:
                self.error_occurred.emit(f"实时流异常: {e}")
                self.log_message.emit("ERROR", f"实时流异常: {e}")
                time.sleep(0.5)

            # 帧率节流
            if self._frame_interval > 0:
                wait = self._frame_interval - (time.time() - t0)
                if wait > 0:
                    self._stop_evt.wait(wait)

        self._running = False
        self.state_changed.emit(False)

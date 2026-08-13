# -*- coding: utf-8 -*-
"""
PC 本地推理引擎（双推理源：本地模型无需上传 Nano 即可检测）
============================================================
- .pt    → ultralytics YOLO 加载推理（PC CPU，需安装 ultralytics）
- .onnx  → onnxruntime 推理（零训练环境依赖）
- 单图异步推理：LocalInferEngine.detect(image_path, model_path) 信号回传
- 实时流同步推理：load_session() + infer_frame() 供 stream_engine "local" 模式

输出格式与 Nano detect_response 一致：
  {"detections": [{"box":[x1,y1,x2,y2], "confidence":f, "class_id":i, "result":"NG"}], ...}
"""
import os
import time
import threading

import numpy as np
import cv2

from PyQt5.QtCore import QObject, pyqtSignal

# NEU-DET 类别名（与 core/controller.py 保持一致）
NEU_CLASSES = [
    "crazing", "inclusion", "patches",
    "pitted_surface", "rolled-in_scale", "scratches",
]


class LocalInferEngine(QObject):
    """本地模型单图推理引擎（线程安全：信号回传结果）"""

    result_ready = pyqtSignal(dict)   # 推理结果（detections + timing + model）
    error_ready = pyqtSignal(str)     # 错误信息
    status_changed = pyqtSignal(bool)  # 推理中 True/空闲 False

    def __init__(self, parent=None):
        super().__init__(parent)
        self._lock = threading.Lock()
        self._busy = False
        self._cancelled = False

    @property
    def busy(self) -> bool:
        return self._busy

    def detect(self, image_path: str, model_path: str, conf_thres=0.25, iou_thres=0.45):
        """异步执行本地推理（后台线程，不阻塞 UI）"""
        if self._busy:
            self.error_ready.emit("本地推理正在进行中，请稍候")
            return
        self._cancelled = False
        self._busy = True
        self.status_changed.emit(True)
        threading.Thread(
            target=self._worker, daemon=True,
            args=(image_path, model_path, conf_thres, iou_thres),
        ).start()

    def cancel(self):
        """请求取消当前推理；实际线程不会中断，但结果不会被发出"""
        self._cancelled = True

    # ---------- 后台线程 ----------
    def _worker(self, image_path, model_path, conf_thres, iou_thres):
        try:
            # cv2.imread 不支持中文路径，用 np.fromfile + imdecode 替代
            import numpy as np
            raw = np.fromfile(image_path, dtype=np.uint8)
            img = cv2.imdecode(raw, cv2.IMREAD_COLOR)
            if img is None:
                raise RuntimeError(f"无法读取图片: {image_path}")

            ext = os.path.splitext(model_path)[1].lower()
            t0 = time.perf_counter()
            if ext == ".onnx":
                dets, timing = self._infer_onnx(img, model_path, conf_thres, iou_thres)
            else:  # .pt 及其他（默认 ultralytics）
                dets, timing = self._infer_ultralytics(img, model_path, conf_thres, iou_thres)

            # 若用户已点击停止，抑制结果渲染
            if not self._cancelled:
                result = {
                    "detections": dets,
                    "timing": timing,
                    "model": os.path.basename(model_path),
                    "source": "local",
                    "ok": True,
                }
                self.result_ready.emit(result)
        except Exception as e:
            if not self._cancelled:
                import traceback
                traceback.print_exc()
                self.error_ready.emit(f"本地推理失败: {e}")
        finally:
            self._busy = False
            self._cancelled = False
            self.status_changed.emit(False)

    # ---------- ultralytics (.pt) ----------
    def _infer_ultralytics(self, img, model_path, conf, iou):
        from ultralytics import YOLO
        t0 = time.perf_counter()
        model = YOLO(model_path)
        t_load = time.perf_counter()
        results = model.predict(
            img, conf=conf, iou=iou, verbose=False,
            imgsz=640, device="cpu",
        )
        t_infer = time.perf_counter()
        r = results[0]
        dets = []
        if r.boxes is not None and len(r.boxes) > 0:
            boxes = r.boxes.xyxy.cpu().numpy()
            confs = r.boxes.conf.cpu().numpy()
            cls_ids = r.boxes.cls.cpu().numpy().astype(int)
            for box, c, ci in zip(boxes, confs, cls_ids):
                x1, y1, x2, y2 = [float(v) for v in box]
                dets.append({
                    "box": [x1, y1, x2, y2],
                    "confidence": float(c),
                    "class_id": int(ci),
                    "result": "NG",
                })
        timing = {
            "load_ms": round((t_load - t0) * 1000, 1),
            "infer_ms": round((t_infer - t_load) * 1000, 1),
            "total_ms": round((t_infer - t0) * 1000, 1),
        }
        return dets, timing

    # ---------- onnxruntime (.onnx) ----------
    def _infer_onnx(self, img, model_path, conf, iou):
        import onnxruntime as ort
        from core.local_postprocess import yolo_postprocess

        session = ort.InferenceSession(model_path, providers=["CPUExecutionProvider"])
        input_name = session.get_inputs()[0].name
        input_shape = session.get_inputs()[0].shape  # [1,3,640,640]

        # letterbox 预处理
        h0, w0 = img.shape[:2]
        target = 640
        scale = min(target / w0, target / h0)
        new_w, new_h = int(w0 * scale), int(h0 * scale)
        resized = cv2.resize(img, (new_w, new_h), interpolation=cv2.INTER_LINEAR)
        padded = np.full((target, target, 3), 114, dtype=np.uint8)
        pad_w = (target - new_w) // 2
        pad_h = (target - new_h) // 2
        padded[pad_h:pad_h + new_h, pad_w:pad_w + new_w] = resized
        blob = cv2.cvtColor(padded, cv2.COLOR_BGR2RGB).astype(np.float32) / 255.0
        blob = np.transpose(blob, (2, 0, 1))[None]

        t0 = time.perf_counter()
        outputs = session.run(None, {input_name: blob})
        t1 = time.perf_counter()
        out = outputs[0]  # (1, 84, 8400) 或 (1, 8400, 84)

        dets = yolo_postprocess(out, scale, pad_w, pad_h, conf, iou)
        timing = {
            "infer_ms": round((t1 - t0) * 1000, 1),
            "total_ms": round((t1 - t0) * 1000, 1),
        }
        return dets, timing


# ================= 实时流同步推理（stream_engine "local" 模式） =================

_session_cache = {}
_session_lock = threading.Lock()


def load_session(model_path: str):
    """加载 ONNX session（按绝对路径缓存）；模型不存在/损坏抛异常"""
    key = os.path.abspath(model_path)
    with _session_lock:
        s = _session_cache.get(key)
        if s is None:
            import onnxruntime as ort
            s = ort.InferenceSession(model_path, providers=["CPUExecutionProvider"])
            _session_cache[key] = s
        return s


def clear_session_cache():
    """清空 session 缓存（切换模型/内存回收时调用）"""
    with _session_lock:
        _session_cache.clear()


def infer_frame(session, frame, conf_thres=0.25, iou_thres=0.45) -> list:
    """对 BGR 帧执行 ONNX 推理 → UI 格式 [(cls, conf, x1, y1, x2, y2), ...]"""
    from core.local_postprocess import yolo_postprocess

    h0, w0 = frame.shape[:2]
    target = 640
    scale = min(target / w0, target / h0)
    new_w, new_h = int(w0 * scale), int(h0 * scale)
    resized = cv2.resize(frame, (new_w, new_h), interpolation=cv2.INTER_LINEAR)
    padded = np.full((target, target, 3), 114, dtype=np.uint8)
    pad_w = (target - new_w) // 2
    pad_h = (target - new_h) // 2
    padded[pad_h:pad_h + new_h, pad_w:pad_w + new_w] = resized
    blob = cv2.cvtColor(padded, cv2.COLOR_BGR2RGB).astype(np.float32) / 255.0
    blob = np.transpose(blob, (2, 0, 1))[None]

    outputs = session.run(None, {session.get_inputs()[0].name: blob})
    raw = yolo_postprocess(outputs[0], scale, pad_w, pad_h, conf_thres, iou_thres)

    ui = []
    for d in raw:
        cid = d["class_id"]
        cls = NEU_CLASSES[cid] if cid < len(NEU_CLASSES) else f"cls{cid}"
        ui.append((cls, d["confidence"], *d["box"]))
    return ui

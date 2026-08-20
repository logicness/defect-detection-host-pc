"""
NG 图片自动保存
检出缺陷时异步保存：原图 + 画框标注图 + CSV 记录
路径: {save_root}/NG/YYYYMMDD/；低置信案例另存 LOW_CONF/
后台写盘线程，入队即返回不阻塞 UI
"""
import csv
import logging
import os
import queue
import threading
import time
from datetime import datetime

import cv2

log = logging.getLogger(__name__)


def _imwrite_unicode(path: str, img) -> bool:
    """cv2.imwrite 内部用 fopen 不支持中文路径（静默失败），
    改用 imencode + 二进制写，支持任意路径。"""
    try:
        ext = os.path.splitext(path)[1] or ".png"
        ok, buf = cv2.imencode(ext, img)
        if not ok:
            return False
        with open(path, "wb") as f:
            f.write(buf.tobytes())
        return True
    except Exception as e:
        log.warning(f"图片写入失败 {path}: {e}")
        return False


class NGSaver:
    def __init__(self, save_root: str = "D:/Inspect/Images"):
        self.save_root = save_root
        self._q = queue.Queue(maxsize=200)
        self._thread = threading.Thread(target=self._worker, daemon=True, name="ng-saver")
        self._thread.start()
        self.stats = {"saved": 0, "dropped": 0, "failed": 0}

    def set_save_root(self, path: str):
        self.save_root = path

    # ---------------- 异步接口 ----------------
    def enqueue_save(self, frame, dets: list, frame_id: int = 0) -> bool:
        if frame is None or not dets:
            return False
        try:
            self._q.put_nowait(("ng", frame, dets, frame_id))
            return True
        except queue.Full:
            self.stats["dropped"] += 1
            return False

    def enqueue_low_conf(self, frame, dets: list, frame_id: int = 0,
                         conf_thresh: float = 0.5) -> bool:
        low = [d for d in dets if d[1] < conf_thresh]
        if frame is None or not low:
            return False
        try:
            # 只归档低置信子集，避免把高置信框也写进 LOW_CONF
            self._q.put_nowait(("low", frame, low, frame_id))
            return True
        except queue.Full:
            self.stats["dropped"] += 1
            return False

    # ---------------- 同步实现 ----------------
    def save(self, frame, dets: list, frame_id: int, kind: str = "NG"):
        day = datetime.now().strftime("%Y%m%d")
        base = os.path.join(self.save_root, kind, day)
        try:
            os.makedirs(base, exist_ok=True)
        except OSError as e:
            log.warning(f"NG 目录创建失败 {base}: {e}")
            self.stats["failed"] = self.stats.get("failed", 0) + 1
            return None
        ts = datetime.now().strftime("%H%M%S_%f")[:-3]
        orig = os.path.join(base, f"{kind}_{ts}_{frame_id}.png")
        ann = os.path.join(base, f"{kind}_{ts}_{frame_id}_ann.png")
        ok_orig = _imwrite_unicode(orig, frame)
        ann_img = frame.copy()
        for d in dets:
            cls, conf, x1, y1, x2, y2 = d[:6]
            cv2.rectangle(ann_img, (int(x1), int(y1)), (int(x2), int(y2)), (0, 0, 255), 2)
            cv2.putText(ann_img, f"{cls} {conf:.2f}", (int(x1), int(y1) - 6),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 255), 1)
        ok_ann = _imwrite_unicode(ann, ann_img)
        # 图片保存失败时不再写 CSV（避免记录与实际不符），并计入 failed
        if not (ok_orig and ok_ann):
            self.stats["failed"] = self.stats.get("failed", 0) + 1
            log.warning(f"NG 图片保存失败，跳过 CSV 记录: {orig}")
            return None
        csv_path = os.path.join(base, "records.csv")
        try:
            with open(csv_path, "a", newline="", encoding="utf-8-sig") as f:
                w = csv.writer(f)
                if os.path.getsize(csv_path) == 0:
                    w.writerow(["time", "class", "conf", "x1", "y1", "x2", "y2", "file"])
                for d in dets:
                    w.writerow([datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                                d[0], d[1], d[2], d[3], d[4], d[5], os.path.basename(orig)])
        except OSError as e:
            log.warning(f"NG CSV 写入失败 {csv_path}: {e}")
            self.stats["failed"] = self.stats.get("failed", 0) + 1
            return None
        self.stats["saved"] += 1
        return orig, ann

    def _worker(self):
        while True:
            try:
                task = self._q.get(timeout=1.0)
            except queue.Empty:
                continue
            if task is None:
                break
            try:
                kind, frame, dets, fid = task
                self.save(frame, dets, fid, kind="NG" if kind == "ng" else "LOW_CONF")
            except Exception as e:
                # 不再吞异常：记录日志并计数，故障可见
                self.stats["failed"] = self.stats.get("failed", 0) + 1
                log.warning(f"NG 保存任务异常: {e}")

    def close(self):
        try:
            # 队列满时不能阻塞 close（worker 可能正卡在写盘）
            try:
                self._q.put_nowait(None)
            except queue.Full:
                pass
            self._thread.join(timeout=3.0)
        except Exception:
            pass

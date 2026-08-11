"""
帧源抽象 + 模拟帧源（相机到货前使用）
SimFrameSource 渲染轴承工件合成图（金属轴+轴承环+旋转高光），
周期性生成缺陷（暗色划痕），并经 last_defects 暴露缺陷框供模拟推理使用
"""
import math
import time

import cv2
import numpy as np


class FrameSource:
    """帧源基类：open/read/close 接口"""

    def open(self) -> bool:
        raise NotImplementedError

    def read(self):
        """返回 (ok, frame)"""
        raise NotImplementedError

    def close(self):
        pass

    # 当前帧的缺陷框（模拟推理用），真实相机子类返回 []
    last_defects: list = []


class SimFrameSource(FrameSource):
    """合成轴承工件视频流：640x480，周期缺陷"""

    def __init__(self, width: int = 640, height: int = 480):
        self.width, self.height = width, height
        self._i = 0
        self._opened = False
        self.last_defects = []

    def open(self) -> bool:
        self._opened = True
        self._i = 0
        return True

    def close(self):
        self._opened = False

    def read(self):
        if not self._opened:
            return False, None
        i = self._i
        self._i += 1
        frame = self._render(i)
        self.last_defects = self._defects_for(i)
        return True, frame

    # ---------------- 渲染 ----------------
    def _render(self, i: int) -> np.ndarray:
        h, w = self.height, self.width
        frame = np.full((h, w, 3), 38, dtype=np.uint8)
        # 背景竖向渐变
        grad = np.linspace(30, 52, h, dtype=np.uint8)
        frame[:, :, 0] = grad[:, None]
        frame[:, :, 1] = grad[:, None]
        frame[:, :, 2] = grad[:, None]

        cx, cy = w // 2, h // 2
        # 水平轴身（金属渐变）
        shaft = np.zeros((260, w - 80, 3), dtype=np.uint8)
        prof = (np.sin(np.linspace(0, math.pi, 260)) * 130 + 60).astype(np.uint8)
        shaft[:, :, 0] = prof[:, None]
        shaft[:, :, 1] = prof[:, None]
        shaft[:, :, 2] = prof[:, None]
        frame[cy - 130:cy + 130, 40:w - 40] = shaft

        # 轴承环：外圈/内圈/孔
        cv2.circle(frame, (cx, cy), 155, (175, 175, 175), -1)
        cv2.circle(frame, (cx, cy), 150, (200, 200, 200), 2)
        cv2.circle(frame, (cx, cy), 112, (105, 105, 105), -1)
        cv2.circle(frame, (cx, cy), 66, (28, 28, 28), -1)
        cv2.circle(frame, (cx, cy), 66, (70, 70, 70), 2)

        # 旋转高光弧（模拟工件转动）
        ang = (i * 6) % 360
        cv2.ellipse(frame, (cx, cy), (133, 133), 0, ang, ang + 50, (235, 235, 235), 10)
        cv2.ellipse(frame, (cx, cy), (133, 133), 0, ang + 180, ang + 210, (60, 60, 60), 10)

        # 缺陷划痕（暗色弧）
        if self._defects_for(i):
            cv2.ellipse(frame, (cx + 115, cy + 40), (26, 14), 30, 0, 160, (18, 18, 20), 5)
        return frame

    @staticmethod
    def _defects_for(i: int) -> list:
        """每 8 帧出现 2 帧缺陷（约 25% NG 率的演示节奏）"""
        if i % 8 in (6, 7):
            return [(408, 252, 462, 306)]
        return []


def sim_detect(frame: np.ndarray, source: FrameSource) -> list:
    """模拟推理：依据帧源缺陷框生成 UI 格式检测结果
    返回 [(cls_name, conf, x1, y1, x2, y2), ...]
    """
    import random
    defects = getattr(source, "last_defects", []) or []
    out = []
    for (x1, y1, x2, y2) in defects:
        cls = random.choice(["scratches", "scratches", "crazing", "inclusion"])
        conf = round(random.uniform(0.86, 0.99), 2)
        out.append((cls, conf, x1, y1, x2, y2))
    return out

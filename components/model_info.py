# -*- coding: utf-8 -*-
"""
模型信息提取与标签（从 model_library.py 拆出，避免文件锁冲突）
- 数据集/类别数/预训练检测/推荐标记
"""
import os
import re

from components.model_library import score_quality, load_library

# 常见数据集/项目名称映射（路径/文件名中的关键字 → 显示名）
_DATASET_MAP = [
    ("neu-det", "NEU-DET"),
    ("neu_yolov8", "NEU-DET"),
    ("neu", "NEU-DET"),
    ("mt_", "MT"),
    ("mvit_", "MVIT"),
    ("guangdong_", "广东铝"),
    ("guangdong", "广东铝"),
    ("steeldefectx", "SteelDefectX"),
    ("steel_defect", "SteelDefectX"),
    ("crazing", "NEU-DET"),
    ("inclusion", "NEU-DET"),
    ("patches", "NEU-DET"),
    ("pitted_surface", "NEU-DET"),
    ("rolled-in_scale", "NEU-DET"),
    ("scratches", "NEU-DET"),
    ("yolov8n", "COCO 预训练"),
    ("yolov8s", "COCO 预训练"),
    ("yolov8m", "COCO 预训练"),
    ("yolov8l", "COCO 预训练"),
    ("yolov8x", "COCO 预训练"),
    ("yolov5n", "COCO 预训练"),
    ("yolov5s", "COCO 预训练"),
    ("yolov5m", "COCO 预训练"),
    ("yolov5l", "COCO 预训练"),
    ("yolov5x", "COCO 预训练"),
]

# 预训练模型文件名（直接下载的 backbone，不适合缺陷检测）
_PRETRAINED_NAMES = {
    "yolov8n.pt", "yolov8s.pt", "yolov8m.pt", "yolov8l.pt", "yolov8x.pt",
    "yolov5n.pt", "yolov5s.pt", "yolov5m.pt", "yolov5l.pt", "yolov5x.pt",
    "yolov8n.onnx", "yolov8s.onnx", "yolov8m.onnx", "yolov8l.onnx", "yolov8x.onnx",
    "yolov5n.onnx", "yolov5s.onnx", "yolov5m.onnx", "yolov5l.onnx", "yolov5x.onnx",
}


def extract_dataset(path: str) -> str:
    """从路径/文件名推断训练数据集/项目名称，返回可读名称或 ''"""
    plow = path.replace("\\", "/").lower()
    name = os.path.basename(path).lower()
    for key, display in _DATASET_MAP:
        if key in plow or key in name:
            return display
    return ""


def extract_classes(path: str) -> int:
    """从文件名提取类别数（如 '_6类' '_25c' '25c' 等），找不到返回 0。
    对已知数据集做默认兜底（NEU-DET=6 类）。"""
    name = os.path.basename(path)
    m = re.search(r"[_\-]?(\d+)\s*类", name)
    if m:
        return int(m.group(1))
    m = re.search(r"[_\-](\d+)c\b", name, re.I)
    if m:
        return int(m.group(1))
    m = re.search(r"(\d+)c\b", name, re.I)
    if m:
        return int(m.group(1))
    # 兜底：已知数据集默认类别数
    dataset = extract_dataset(path)
    if dataset == "NEU-DET":
        return 6
    return 0


def is_pretrained(path: str) -> bool:
    """判断是否为下载的预训练 backbone（非缺陷检测专用）"""
    name = os.path.basename(path).lower()
    return name in _PRETRAINED_NAMES


def model_tags(path: str) -> dict:
    """汇总模型标签信息：dataset, classes, is_pretrained, quality_label, score, is_recommended"""
    label, score, color, tip = score_quality(path)
    dataset = extract_dataset(path)
    classes = extract_classes(path)
    pretrained = is_pretrained(path)
    recommended = (
        not pretrained and
        score is not None and score >= 80 and
        bool(dataset)
    )
    return {
        "dataset": dataset,
        "classes": classes,
        "is_pretrained": pretrained,
        "quality_label": label,
        "score": score,
        "color": color,
        "tip": tip,
        "is_recommended": recommended,
    }


def find_library_item(path: str) -> dict:
    """按路径查找模型库条目，不存在返回 None"""
    for e in load_library():
        if e.get("path", "") == path:
            return e
    return None

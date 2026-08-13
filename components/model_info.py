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
    ("广东铝", "广东铝"),
    ("铝型材", "广东铝"),
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


# ---------------- 训练图片目录推断 ----------------
_TRAIN_ROOT = r"D:\RK3568&Orin Nano\ORIN NANO\Model Training"

# 数据集显示名 → 候选图片目录（按优先级排列，取第一个存在且含图片的）
_DATASET_IMAGE_CANDIDATES = {
    "NEU-DET": [
        rf"{_TRAIN_ROOT}\_archive_20260811\datasets\NEU_FIXED_6类_1800张_补标版\images\train",
        rf"{_TRAIN_ROOT}\_archive_20260811\datasets\NEU_6类_1800张_钢材缺陷\images\train",
        rf"{_TRAIN_ROOT}\_archive_20260811\datasets\NEU_FIXED_6类_1800张_补标版\images",
        rf"{_TRAIN_ROOT}\_archive_20260811\datasets\NEU_6类_1800张_钢材缺陷\images",
    ],
    "SteelDefectX": [
        rf"{_TRAIN_ROOT}\_archive_20260811\datasets\SteelDefectX_YOLO_25类_7764张\train\images",
        rf"{_TRAIN_ROOT}\datasets\Merged_SDX_NEU\train\images",
        rf"{_TRAIN_ROOT}\datasets\Merged_SDX_NEU\val\images",
    ],
    "MVIT": [
        rf"{_TRAIN_ROOT}\_archive_20260811\datasets\MVIT_3子集_1810张_真实产线\ready\casting_billet\images",
        rf"{_TRAIN_ROOT}\_archive_20260811\datasets\MVIT_3子集_1810张_真实产线\ready\mhpsds\images",
        rf"{_TRAIN_ROOT}\_archive_20260811\datasets\MVIT_3子集_1810张_真实产线\ready\steel_pipe\images",
        rf"{_TRAIN_ROOT}\_archive_20260811\datasets\MVIT_3子集_1810张_真实产线\ready",
    ],
    "广东铝": [
        rf"{_TRAIN_ROOT}\_archive_20260811\datasets\天池铝型材_11类_2618张_分类格式\铝型材表面瑕疵数据集",
        rf"{_TRAIN_ROOT}\_archive_20260811\datasets\天池铝型材_11类_2618张_分类格式",
    ],
    "MT": [
        rf"{_TRAIN_ROOT}\_archive_20260811\datasets\MT_cls_6类_1344张_磁瓦分类",
    ],
}

_IMAGE_EXTS = (".jpg", ".jpeg", ".png", ".bmp")


def _dir_has_images(d: str) -> bool:
    """判断目录本身或向下 2 层子目录中是否含有图片文件"""
    if not os.path.isdir(d):
        return False
    try:
        dirs = [d]
        for _ in range(2):
            nxt = []
            for cur in dirs:
                for entry in os.listdir(cur):
                    p = os.path.join(cur, entry)
                    if os.path.isfile(p) and p.lower().endswith(_IMAGE_EXTS):
                        return True
                    if os.path.isdir(p):
                        nxt.append(p)
            dirs = nxt
            if not dirs:
                break
    except OSError:
        return False
    return False


def resolve_dataset_image_dir(model_path: str) -> str:
    """根据模型文件路径推断其训练数据集对应的图片目录，找不到返回 ''。

    优先按数据集名匹配已知目录：第一个含图片的目录直接返回；
    若候选目录存在但无图片（如仅含压缩包），返回第一个存在的候选目录；
    最后在模型所在目录附近寻找 images/。
    """
    if not model_path:
        return ""
    dataset = extract_dataset(model_path)
    existing_fallback = ""
    for cand in _DATASET_IMAGE_CANDIDATES.get(dataset, []):
        if os.path.isdir(cand):
            if _dir_has_images(cand):
                return cand
            if not existing_fallback:
                existing_fallback = cand
    if existing_fallback:
        return existing_fallback
    # 通用兜底：模型文件所在目录附近找图片
    base = os.path.dirname(model_path)
    for cand in (
        os.path.join(base, "images", "train"),
        os.path.join(base, "images"),
        os.path.join(base, "train", "images"),
        base,
    ):
        if _dir_has_images(cand):
            return cand
    return ""

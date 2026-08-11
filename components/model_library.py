# -*- coding: utf-8 -*-
"""
模型库公共工具（对齐 Nano 版）
==============================
- 模型库持久化：data/model_library.json（项目目录优先，写入失败回退用户主目录）
- 模型质量判定：按路径/名称前缀区分 生产/基线/待编译/编译产物/候选
- 默认本地模型目录探测
- 文件扩展名常量
"""
import os

_LOCAL_EXTS = (".engine", ".onnx", ".pt")

_DEFAULT_LOCAL_DIR = "D:/Inspect/Models"

# 候选目录（自动探测 PC 本地模型常见位置）
_DETECT_DIRS = [
    r"D:\RK3568&Orin Nano\ORIN NANO\Model Training\runs",
    r"D:\RK3568&Orin Nano\RK3568\Model Training\runs",
    _DEFAULT_LOCAL_DIR,
    r"D:\RK3568&Orin Nano\ORIN NANO\Model Training",
]

# ---------- 模型质量判定（下位机模型清单） ----------
_MODEL_QUALITY_RULES = [
    ("neu/", "★ 生产", "#22c55e",
     "NEU 数据集训练的生产模型，项目缺陷检测专用"),
    ("baseline/", "基线", "#eab308",
     "COCO 预训练通用模型，不识别 NEU 缺陷类别"),
    ("uploads/", "待编译", "#3b82f6",
     "刚上传的 onnx/pt，编译为 engine 前不可用"),
    ("compiled/", "编译产物", "#a855f7",
     "上传后自动编译的中间产物"),
]


def model_quality(name: str):
    """按模型路径返回 (标签, 颜色, 说明)；未知返回 (未知, 灰, '')"""
    for prefix, label, color, tip in _MODEL_QUALITY_RULES:
        if name.startswith(prefix):
            return label, color, tip
    return "未知", "#94a3b8", "无法识别来源目录"


def infer_quality_from_path(path: str):
    """对 PC 路径做质量推断，返回 (标签, 颜色)"""
    plow = path.replace("\\", "/").lower()
    if "/neu" in plow or "e1003" in plow or "neu_yolov8" in plow:
        return "★ 生产", "#22c55e"
    elif "baseline" in plow:
        return "基线", "#eab308"
    elif "uploads" in plow:
        return "待编译", "#3b82f6"
    elif "compiled" in plow:
        return "编译产物", "#a855f7"
    else:
        return "候选", "#60a5fa"


def detect_default_local_dir() -> str:
    """自动探测 PC 本地模型目录"""
    for d in _DETECT_DIRS:
        if os.path.isdir(d):
            return d
    return _DEFAULT_LOCAL_DIR


# ---------- 模型库持久化 ----------
def _library_path() -> str:
    data_dir = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
    return os.path.join(data_dir, "model_library.json")


def load_library() -> list:
    """读取模型库（JSON 列表，字段 path/name/size_mb）"""
    import json
    try:
        with open(_library_path(), "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except Exception:
        return []


def save_library(items: list) -> str:
    """保存模型库，返回实际写入路径（项目 data/ 优先，失败回退用户主目录）"""
    import json
    fallback = os.path.join(
        os.path.expanduser("~"), ".workbuddy_model_library.json")
    last_err = None
    for path in (_library_path(), fallback):
        try:
            tmp = path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(items, f, ensure_ascii=False, indent=2)
            os.replace(tmp, path)
            return path
        except Exception as e:
            last_err = e
            try:
                if os.path.exists(path + ".tmp"):
                    os.remove(path + ".tmp")
            except Exception:
                pass
    raise OSError(f"模型库写入失败: {_library_path()}\n{last_err}")


def scan_model_dir(directory: str) -> list:
    """递归扫描目录下所有模型文件，返回 [(相对路径, 绝对路径, 大小MB), ...] 排序结果"""
    if not os.path.isdir(directory):
        return []
    files = []
    try:
        for root, dirs, fnames in os.walk(directory):
            dirs[:] = [d for d in dirs if not d.startswith((".", "__"))]
            for fn in sorted(fnames):
                if fn.lower().endswith(_LOCAL_EXTS):
                    p = os.path.join(root, fn)
                    if os.path.isfile(p):
                        rel = os.path.relpath(p, directory).replace("\\", "/")
                        files.append((rel, p, os.path.getsize(p) / 1e6))
    except OSError:
        return []
    return files

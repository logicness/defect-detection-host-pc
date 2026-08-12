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
import re

_LOCAL_EXTS = (".engine", ".onnx", ".pt")

_DEFAULT_LOCAL_DIR = "D:/Inspect/Models"

# 候选目录（自动探测 PC 本地模型常见位置）
_DETECT_DIRS = [
    r"D:\RK3568&Orin Nano\ORIN NANO\Model Training\runs",
    r"D:\RK3568&Orin Nano\RK3568\Model Training\runs",
    _DEFAULT_LOCAL_DIR,
    r"D:\RK3568&Orin Nano\ORIN NANO\Model Training",
]

# ---------- 模型质量评分（高/中/低/未知） ----------
_QUALITY_LABELS = {
    "高": ("#22c55e", "性能指标优秀，推荐用于生产检测"),
    "中": ("#eab308", "性能指标一般，可作为备选或继续调优"),
    "低": ("#ef4444", "性能指标偏低，建议重新训练或选用其他模型"),
    "未知": ("#94a3b8", "无法从文件名/路径推断性能指标"),
}


def _extract_metric_score(path: str) -> tuple:
    """从路径/文件名提取性能指标分数 (0-100) 与依据说明。

    解析规则（按优先级）：
    1. 文件名中显式出现 mAP/mAP50/acc/precision 等关键字后跟 0.x 数字 → 直接作为指标。
    2. 文件名中出现 0.50~0.99 的孤立数字 → 视为 mAP，取最大值。
    3. 文件名含 best → 默认 85 分（训练最佳权重）。
    4. 文件名含 last → 默认 60 分（训练末尾权重）。
    5. 路径含 NEU 训练产物（/neu/、e1003 等）→ 在以上基础 +5 分（上限 95）。
    6. 完全无信息 → 返回 None。
    """
    name = os.path.basename(path)
    nlow = name.lower()
    plow = path.replace("\\", "/").lower()

    score = None
    reason = ""

    # 1) 显式指标关键字
    metric_pat = re.compile(
        r"(map50|mAP50|map|mAP|acc|accuracy|precision|recall|f1)"
        r"[_\-]?(\d+\.?\d*)", re.I)
    matches = metric_pat.findall(name)
    if matches:
        vals = []
        for _, v in matches:
            try:
                vals.append(float(v))
            except ValueError:
                continue
        if vals:
            # 若数字>1 且<=100，视为百分比
            raw = max(vals)
            if raw > 1 and raw <= 100:
                score = raw
            else:
                score = raw * 100
            reason = f"文件名指标 {max(vals):.2f}"

    # 2) 未找到显式指标，取 0.50~0.99 孤立数字作为 mAP
    if score is None:
        nums = [float(x) for x in re.findall(r"\b(0\.\d{2,3})\b", name)]
        nums = [x for x in nums if 0.50 <= x <= 0.99]
        if nums:
            score = max(nums) * 100
            reason = f"文件名推断 mAP={max(nums):.2f}"

    # 3) best / last 默认分
    if score is None:
        if "best" in nlow:
            score = 85
            reason = "best 权重（训练最佳）"
        elif "last" in nlow:
            score = 60
            reason = "last 权重（训练末尾）"

    # 4) NEU 项目训练目录加分
    is_neu = "/neu" in plow or "e1003" in plow or "neu_yolov8" in plow
    if score is not None and is_neu:
        score = min(95, score + 5)
        reason += "，NEU 项目训练产物"

    return score, reason


def score_quality(path: str) -> tuple:
    """返回 (标签, 分数, 颜色, 说明)。

    分数区间：
    - 高：>= 80
    - 中：60 ~ 79
    - 低：< 60
    - 未知：无法推断
    """
    score, reason = _extract_metric_score(path)
    if score is None:
        label = "未知"
        color, tip = _QUALITY_LABELS[label]
        return label, 0, color, tip
    if score >= 80:
        label = "高"
    elif score >= 60:
        label = "中"
    else:
        label = "低"
    color, tip = _QUALITY_LABELS[label]
    detail = f"{reason}，综合得分 {score:.0f}：{tip}"
    return label, int(score), color, detail


def model_quality(name: str):
    """按模型路径返回质量标签（兼容旧接口，用于下位机模型清单）。"""
    label, score, color, tip = score_quality(name)
    if label == "未知" and "/" in name:
        # 对下位机路径，按目录前缀做兜底判断
        plow = name.lower()
        if plow.startswith("neu/"):
            return "高", "#22c55e", "NEU 生产模型（目录推断）"
        if plow.startswith("baseline/"):
            return "中", "#eab308", "COCO 预训练基线模型"
        if plow.startswith("uploads/"):
            return "低", "#ef4444", "刚上传尚未编译，未经验证"
        if plow.startswith("compiled/"):
            return "中", "#eab308", "编译产物，待验证"
    display = f"{label} ({score})" if score else label
    return display, color, tip


def infer_quality_from_path(path: str):
    """对 PC 路径做质量推断（兼容旧接口）。"""
    label, score, color, tip = score_quality(path)
    display = f"{label} ({score})" if score else label
    return display, color


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

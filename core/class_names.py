# -*- coding: utf-8 -*-
"""
数据集类别名统一注册表
========================
上位机 class_id → 类别名 映射的唯一来源。此前各处硬编码 NEU-DET 6 类名，
导致加载 SteelDefectX(25类)/MVIT(连铸坯6类等) 模型时类别名错位或退化为 clsN。

新增模型/数据集时，在此登记类别表并在 resolve_class_names() 加匹配规则即可。
"""
import os

# NEU-DET 6 类（钢材表面缺陷）
NEU_CLASSES = [
    "crazing", "inclusion", "patches",
    "pitted_surface", "rolled-in_scale", "scratches",
]

# SteelDefectX 25 类（连铸坯/钢材表面缺陷，类别名与 data.yaml 一致）
STEELDEFECTX_CLASSES = [
    "Bright scratch", "Crazing", "Crease", "Crescent gap", "Dark scratches",
    "Finishing roll printing", "Inclusion", "Iron scale compression",
    "Iron sheet ash", "Oil spot", "Oxide scale of plate system",
    "Oxide scale of temperature system", "Patches", "Pitted surface", "Punching",
    "Red iron sheet", "Rolled in scale", "Rolled pit", "Secondary rust skin",
    "Silk spot", "Slag inclusion", "Waist folding", "Water spot",
    "Welding line", "White rust",
]

# MVIT 三子集（真实产线）
MVIT_CASTING_BILLET_CLASSES = [
    "scratch", "weld_slag", "cutting_opening",
    "water_slag_mark", "slag_skin", "longitudinal_crack",
]
MVIT_MHPSDS_CLASSES = ["inclusion", "blocky_scale", "striated_scale", "foreign_obj"]
MVIT_STEEL_PIPE_CLASSES = ["warp", "external_fold", "wrinkle", "scratch"]

# 通用金属 35 类（2026-08-17 训练，SDX 25类 + MVIT 真实产线 10 独立类）
# 类别顺序与 datasets/Universal_Metal/data.yaml 完全一致（id 0-34）
UNIVERSAL_METAL_CLASSES = [
    # --- SDX 25 类 (id 0-24) ---
    "Bright scratch", "Crazing", "Crease", "Crescent gap", "Dark scratches",
    "Finishing roll printing", "Inclusion", "Iron scale compression",
    "Iron sheet ash", "Oil spot", "Oxide scale of plate system",
    "Oxide scale of temperature system", "Patches", "Pitted surface", "Punching",
    "Red iron sheet", "Rolled in scale", "Rolled pit", "Secondary rust skin",
    "Silk spot", "Slag inclusion", "Waist folding", "Water spot",
    "Welding line", "White rust",
    # --- MVIT 10 独立类 (id 25-34) ---
    "cutting_opening", "water_slag_mark", "slag_skin", "longitudinal_crack",
    "warp", "external_fold", "wrinkle", "blocky_scale", "striated_scale",
    "foreign_obj",
]


def resolve_class_names(model_path: str):
    """根据模型文件路径/名称推断其训练数据集类别名表，返回 list[str]。

    匹配优先级（先精确数据集，再泛化）：
      1. 通用金属 35 类（universal / _35c / 35类 / universal_metal）
      2. SteelDefectX（steeldefectx / steel_defect / merged_sdx / _25c / 25类）
      3. MVIT 连铸坯（casting / 连铸 / billet）
      4. MVIT mhpsds / steel_pipe
      5. NEU-DET（neu）
    无法识别时返回空列表（调用方兜底为 clsN）。
    """
    if not model_path:
        return []
    p = (model_path or "").replace("\\", "/").lower()
    name = os.path.basename(p).lower()

    if any(k in name or k in p for k in (
            "universal_metal", "universal", "_35c", "35c", "35类", "univ_")):
        return UNIVERSAL_METAL_CLASSES
    if any(k in name or k in p for k in (
            "steeldefectx", "steel_defect", "merged_sdx", "sdx", "_25c", "25类")):
        return STEELDEFECTX_CLASSES
    if any(k in name or k in p for k in ("casting", "连铸", "billet", "casting_billet")):
        return MVIT_CASTING_BILLET_CLASSES
    if "mhpsds" in name or "mhpsds" in p:
        return MVIT_MHPSDS_CLASSES
    # 钢管：英文 steel_pipe 或中文 钢管（模型文件名可能为中文）
    if any(k in name or k in p for k in ("steel_pipe", "钢管")):
        return MVIT_STEEL_PIPE_CLASSES
    # 中厚板（MVIT plate）
    if any(k in name or k in p for k in ("mvit_plate", "plate", "中厚板")):
        return MVIT_MHPSDS_CLASSES
    if "neu" in name or "neu" in p or "neu-det" in p or "neu_det" in p:
        return NEU_CLASSES
    return []


def class_name_of(class_names: list, class_id: int) -> str:
    """按 class_id 取类别名，越界时兜底为 clsN（与旧行为一致，但不误用 NEU 名）"""
    if class_names and 0 <= class_id < len(class_names):
        return class_names[class_id]
    return f"cls{class_id}"

# -*- coding: utf-8 -*-
"""
上位机模型批量效果测试脚本
==========================
遍历模型库（data/model_library.json）中所有检测模型，对指定测试图片逐张推理，
输出对比表格 + 保存画框结果图，直观对比各模型检测效果。

用法:
    python tools/test_models_effect.py                     # 默认测试 NEU 6类样本
    python tools/test_models_effect.py --images D:/xxx     # 指定测试图目录
    python tools/test_models_effect.py --conf 0.3          # 调整置信度阈值
    python tools/test_models_effect.py --model MVIT        # 只测含 MVIT 的模型
    python tools/test_models_effect.py --save-dir D:/out   # 结果图保存目录

说明:
    - .pt 模型走 ultralytics（首次加载较慢，后续复用）；.onnx 走 onnxruntime
    - yolov8s-cls 为分类模型，自动跳过
    - 输出每模型: 检出数 / 平均置信度 / 单图耗时 / 类别分布
"""
import argparse
import glob
import json
import os
import sys
import time
import traceback

import numpy as np
import cv2

HOST_PC = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HOST_PC)

from core.class_names import resolve_class_names
from core.local_infer import LocalInferEngine  # noqa: F401 (接口参考)


# 默认测试图：NEU 6类各 2 张
DEFAULT_IMAGES = [
    r"D:\RK3568&Orin Nano\ORIN NANO\Model Training\_archive_20260811\datasets\NEU_6类_1800张_钢材缺陷\images\train",
]


def find_default_images(n_per_class=2):
    """从 NEU 数据集每类抽 n 张作为默认测试集"""
    base = DEFAULT_IMAGES[0]
    out = []
    for cls in ["crazing", "inclusion", "patches", "pitted_surface",
                "rolled-in_scale", "scratches"]:
        imgs = sorted(glob.glob(os.path.join(base, f"{cls}_*.jpg")))[:n_per_class]
        out.extend(imgs)
    return out


# 预置数据集快捷路径
_MVIT = r"D:\RK3568&Orin Nano\ORIN NANO\Model Training\_archive_20260811\datasets\MVIT_3子集_1810张_真实产线\ready"
PRESET_DATASETS = {
    "neu": DEFAULT_IMAGES[0],
    "billet": os.path.join(_MVIT, "casting_billet", "images", "train"),
    "pipe": os.path.join(_MVIT, "steel_pipe", "images", "train"),
    "plate": os.path.join(_MVIT, "mhpsds", "images", "train"),
}


def resolve_dataset(args_images):
    """解析 --images / --dataset 为测试图列表"""
    imgs = []
    if getattr(args_images, "dataset", None) and args_images.dataset in PRESET_DATASETS:
        base = PRESET_DATASETS[args_images.dataset]
        imgs = sorted(glob.glob(os.path.join(base, "*.jpg")))[:8]
        print(f"[数据集 {args_images.dataset}] {base}")
    elif args_images.images:
        for p in args_images.images:
            if os.path.isdir(p):
                imgs.extend(sorted(glob.glob(os.path.join(p, "*.jpg"))) +
                            sorted(glob.glob(os.path.join(p, "*.png"))))
            elif os.path.isfile(p):
                imgs.append(p)
    else:
        imgs = find_default_images(2)
    return [i for i in imgs if os.path.exists(i)]


def infer_one(model_path, img_path, conf, iou, class_names):
    """单模型单图推理（同步，复用 core.local_infer 的推理内核）。
    返回 (dets_ui, timing_ms) 或抛异常。
    dets_ui: [(cls_name, conf, x1,y1,x2,y2), ...]
    """
    ext = os.path.splitext(model_path)[1].lower()
    img = cv2.imdecode(np.fromfile(img_path, dtype=np.uint8), cv2.IMREAD_COLOR)
    if img is None:
        raise RuntimeError(f"无法读取图片: {img_path}")

    t0 = time.perf_counter()
    if ext == ".onnx":
        from core.local_infer import load_session, infer_frame
        session = load_session(model_path)
        dets = infer_frame(session, img, conf, iou, class_names=class_names)
    else:
        from ultralytics import YOLO
        model = YOLO(model_path)
        results = model.predict(img, conf=conf, iou=iou, verbose=False,
                                imgsz=640, device="cpu")
        dets = []
        r = results[0]
        if r.boxes is not None and len(r.boxes) > 0:
            for box, c, ci in zip(r.boxes.xyxy.cpu().numpy(),
                                  r.boxes.conf.cpu().numpy(),
                                  r.boxes.cls.cpu().numpy().astype(int)):
                x1, y1, x2, y2 = [float(v) for v in box]
                cls = class_names[ci] if class_names and ci < len(class_names) else f"cls{ci}"
                dets.append((cls, float(c), x1, y1, x2, y2))
    dt_ms = (time.perf_counter() - t0) * 1000
    return dets, dt_ms


def draw_result(img_path, dets):
    """画框标注 → BGR ndarray"""
    img = cv2.imdecode(np.fromfile(img_path, dtype=np.uint8), cv2.IMREAD_COLOR)
    for cls, conf, x1, y1, x2, y2 in dets:
        cv2.rectangle(img, (int(x1), int(y1)), (int(x2), int(y2)), (0, 0, 255), 2)
        cv2.putText(img, f"{cls} {conf:.2f}", (int(x1), max(int(y1) - 5, 18)),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 0, 255), 2)
    return img


def main():
    ap = argparse.ArgumentParser(description="上位机模型批量效果测试")
    ap.add_argument("--images", nargs="*", default=None,
                    help="测试图路径/目录（默认 NEU 6类各2张）")
    ap.add_argument("--dataset", default="", choices=list(PRESET_DATASETS),
                    help="预置数据集: neu/billet(连铸坯)/pipe(钢管)/plate(中厚板)")
    ap.add_argument("--conf", type=float, default=0.25, help="置信度阈值")
    ap.add_argument("--iou", type=float, default=0.45, help="IoU 阈值")
    ap.add_argument("--model", default="", help="只测名称含该关键字的模型")
    ap.add_argument("--save-dir", default="", help="结果图保存目录（默认不保存）")
    ap.add_argument("--no-pt", action="store_true", help="跳过 .pt 模型（只测 onnx）")
    args = ap.parse_args()

    # ---------- 测试图 ----------
    imgs = resolve_dataset(args)
    if not imgs:
        print("没有找到测试图片，请用 --images 指定")
        return 1
    print(f"测试图片: {len(imgs)} 张")
    for i in imgs:
        print(f"  - {os.path.basename(i)}")

    # ---------- 模型列表 ----------
    lib = json.load(open(os.path.join(HOST_PC, "data", "model_library.json"),
                         encoding="utf-8"))
    models = []
    for e in lib:
        p = e.get("path", "")
        if not os.path.exists(p):
            continue
        name = os.path.basename(p).lower()
        stem = os.path.splitext(os.path.basename(p))[0].lower()
        if "yolov8s-cls" in name or stem.endswith("_cls") or "cls" == stem.split("_")[-1]:
            continue  # 分类模型跳过
        if args.model:
            # 支持 | 分隔多关键字（任一匹配即测）
            kws = [k.strip() for k in args.model.split("|") if k.strip()]
            if kws and not any(k.lower() in name for k in kws):
                continue
        if args.no_pt and not p.lower().endswith(".onnx"):
            continue
        models.append(p)
    if not models:
        print("没有可测试的检测模型")
        return 1
    print(f"\n测试模型: {len(models)} 个")

    # ---------- 逐模型测试 ----------
    results = []  # {model, name, rows: [{img, n, conf, time, classes}]}
    for mi, mp in enumerate(models, 1):
        mname = os.path.basename(mp)
        class_names = resolve_class_names(mp)
        print(f"\n{'='*62}\n[{mi}/{len(models)}] {mname}  (类名: {len(class_names)}类)")
        if not class_names:
            print("  ⚠ 未识别数据集类别名，将显示 clsN")

        model_rows = []
        for img in imgs:
            try:
                dets, dt = infer_one(mp, img, args.conf, args.iou, class_names)
                n = len(dets)
                avg_conf = sum(d[1] for d in dets) / n if n else 0
                # 类别分布
                from collections import Counter
                dist = dict(Counter(d[0] for d in dets))
                model_rows.append({
                    "img": os.path.basename(img), "n": n,
                    "conf": avg_conf, "ms": dt, "dist": dist, "dets": dets,
                    "path": img,
                })
                print(f"  {os.path.basename(img):<28} n={n:<2} "
                      f"avg_conf={avg_conf:.2f} {dt:6.0f}ms "
                      f"{dict(dist) if dist else '-'}")
                # 保存结果图
                if args.save_dir:
                    os.makedirs(args.save_dir, exist_ok=True)
                    ann = draw_result(img, dets)
                    out = os.path.join(
                        args.save_dir,
                        f"{os.path.splitext(mname)[0]}_{os.path.splitext(os.path.basename(img))[0]}_ann.jpg")
                    cv2.imencode(".jpg", ann)[1].tofile(out)
            except Exception as e:
                print(f"  {os.path.basename(img):<28} ❌ {e}")
                traceback.print_exc()
                model_rows.append({"img": os.path.basename(img), "n": -1,
                                   "conf": 0, "ms": 0, "dist": {}, "dets": [], "path": img})
        results.append({"model": mname, "rows": model_rows})

    # ---------- 汇总对比表 ----------
    print("\n" + "=" * 62)
    print("汇总对比（每模型: 总检出 / 平均耗时 / 检出图片数）")
    print("=" * 62)
    header = f"{'模型':<42} {'总检出':>6} {'均耗时':>8} {'检出图':>6}"
    print(header)
    print("-" * 62)
    for r in results:
        rows = [x for x in r["rows"] if x["n"] >= 0]
        total_n = sum(x["n"] for x in rows)
        hit = sum(1 for x in rows if x["n"] > 0)
        avg_ms = sum(x["ms"] for x in rows) / len(rows) if rows else 0
        print(f"{r['model']:<42} {total_n:>6} {avg_ms:>7.0f}ms {hit:>5}/{len(rows)}")
    print("-" * 62)
    if args.save_dir:
        print(f"画框结果图已保存至: {args.save_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

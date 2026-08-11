# -*- coding: utf-8 -*-
"""
本地模型自动识别脚本
====================
扫描指定目录或常见目录，自动识别 PC 可使用的 .onnx / .pt 模型。

用法：
    1. 直接运行：
       python tools/auto_scan_models.py
       python tools/auto_scan_models.py --dirs D:/Models E:/Models
       python tools/auto_scan_models.py --max-depth 4 --output models.json

    2. 作为模块导入：
       from tools.auto_scan_models import scan_models, auto_scan
       models = scan_models([r"D:\\Models"], max_depth=3)
       models = auto_scan(max_depth=3)   # 扫描常见目录
"""
import os
import sys
import json
import argparse
from pathlib import Path
from typing import List, Dict

# 默认常见模型目录（用户桌面/文档/项目 models 以及 D/E 盘常见位置）
DEFAULT_DIRS = [
    os.path.expanduser(r"~\Desktop"),
    os.path.expanduser(r"~\Documents"),
    os.path.expanduser(r"~\Downloads"),
]
# 加上项目目录下的 models 和常见盘符根目录
_SCRIPT_DIR = Path(__file__).resolve().parent
_PROJECT_DIR = _SCRIPT_DIR.parent
DEFAULT_DIRS += [
    str(_PROJECT_DIR / "models"),
    r"D:\Models",
    r"E:\Models",
    r"D:\模型",
    r"E:\模型",
    # 用户项目/训练目录（模型主要存放位置）
    r"D:\RK3568&Orin Nano",
    r"E:\RK3568&Orin Nano",
]

SUPPORTED_EXTS = (".onnx", ".pt")

# 扫描时跳过的无用目录（避免卡在依赖/缓存目录里）
SKIP_DIRNAMES = {
    "__pycache__", ".git", ".svn", ".idea", ".vscode",
    "node_modules", "venv", ".venv", "env", "dist", "build",
    "datasets", "Dataset", "dataset", "_trash_",
}


def _friendly_name(path: str) -> str:
    """生成模型友好名称"""
    p = Path(path)
    return f"{p.stem} ({p.suffix})"


def scan_models(directories: List[str], max_depth: int = 5) -> List[Dict[str, str]]:
    """
    递归扫描多个目录，识别 .onnx / .pt 模型。

    Args:
        directories: 要扫描的目录列表
        max_depth:   最大递归深度，防止扫太深卡死

    Returns:
        模型列表，每项包含 name/path/size/ext 等字段
    """
    found = []
    seen = set()
    for directory in directories:
        root = Path(directory).expanduser().resolve()
        if not root.exists() or not root.is_dir():
            continue
        # 用 os.walk 限制递归深度，避免 glob 绝对路径 pattern 报错
        for current_root, dirs, files in os.walk(root):
            # 计算当前深度（0 = root 本身）
            depth = current_root.count(os.sep) - str(root).count(os.sep)
            if depth > max_depth:
                del dirs[:]
                continue
            # 跳过无用/缓存目录，避免扫描卡在依赖目录里
            dirs[:] = [d for d in dirs if d not in SKIP_DIRNAMES]
            for fname in files:
                p = Path(current_root) / fname
                if not p.is_file():
                    continue
                ext = p.suffix.lower()
                if ext not in SUPPORTED_EXTS:
                    continue
                if p in seen:
                    continue
                seen.add(p)
                try:
                    size_bytes = p.stat().st_size
                    size_mb = round(size_bytes / (1024 * 1024), 2)
                except OSError:
                    size_mb = 0.0
                found.append({
                    "name": p.stem,
                    "path": str(p),
                    "ext": ext,
                    "size_mb": size_mb,
                    "folder": str(p.parent),
                })
    # 按路径排序，去重后返回
    found.sort(key=lambda x: x["path"].lower())
    return found


def auto_scan(max_depth: int = 5) -> List[Dict[str, str]]:
    """自动扫描常见目录 + 当前项目 models 目录"""
    dirs = [d for d in DEFAULT_DIRS if os.path.isdir(d)]
    return scan_models(dirs, max_depth=max_depth)


def main():
    parser = argparse.ArgumentParser(description="自动识别 PC 本地 .onnx / .pt 模型")
    parser.add_argument(
        "--dirs", nargs="*",
        help="指定要扫描的目录（可多个），不指定则扫描常见目录")
    parser.add_argument(
        "--max-depth", type=int, default=5,
        help="最大递归深度，默认 5")
    parser.add_argument(
        "--output", type=str, default="",
        help="结果输出到 JSON 文件（可选）")
    args = parser.parse_args()

    if args.dirs:
        models = scan_models(args.dirs, max_depth=args.max_depth)
    else:
        models = auto_scan(max_depth=args.max_depth)

    print(f"共发现 {len(models)} 个本地模型：")
    for m in models:
        print(f"  [{m['ext']}] {m['name']:30s} {m['size_mb']:>8.2f} MB  {m['path']}")

    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            json.dump(models, f, ensure_ascii=False, indent=2)
        print(f"\n结果已保存到: {args.output}")

    return 0 if models else 1


if __name__ == "__main__":
    sys.exit(main())

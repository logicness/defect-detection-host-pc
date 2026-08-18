# -*- coding: utf-8 -*-
"""
检测报告生成（U9，2026-08-14）
将查询结果生成为自包含 HTML 报告：
- 顶部 KPI：总数 / OK / NG / 良率
- 缺陷类型分布表 + 简单条形图（内嵌 SVG）
- NG 图片拼图网格（base64 内嵌，图片缺失显示占位）
"""
import base64
import io
import os
from datetime import datetime

import numpy as np
from PIL import Image

# 尽量用 OpenCV 读图（支持中文路径），无则用 PIL
try:
    import cv2
    _HAVE_CV2 = True
except Exception:
    _HAVE_CV2 = False


def _read_thumbnail(path: str, max_w: int = 200, max_h: int = 200):
    """读图并缩放为缩略图字节，返回 (b64, mime)；失败返回 None"""
    try:
        if _HAVE_CV2 and os.path.isfile(path):
            with open(path, "rb") as f:
                buf = np.frombuffer(f.read(), dtype=np.uint8)
            img = cv2.imdecode(buf, cv2.IMREAD_COLOR)
            if img is None:
                return None
            img = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
            im = Image.fromarray(img)
        elif os.path.isfile(path):
            im = Image.open(path).convert("RGB")
        else:
            return None
        im.thumbnail((max_w, max_h))
        buf = io.BytesIO()
        im.save(buf, format="JPEG", quality=80)
        return base64.b64encode(buf.getvalue()).decode("ascii")
    except Exception:
        return None


def _esc(s) -> str:
    """HTML 转义（报告内容来自数据库，防注入）"""
    return (str(s).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;").replace("'", "&#39;"))


def _defect_bar(dist: dict) -> str:
    """缺陷类型分布 → 内嵌 SVG 横向条形图"""
    if not dist:
        return "<p style='color:#94a3b8'>该时间段无 NG 记录</p>"
    items = sorted(dist.items(), key=lambda kv: -kv[1])[:10]
    mx = max(v for _, v in items) or 1
    rows = []
    for name, cnt in items:
        w = int(cnt / mx * 220)
        safe_name = _esc(name)
        rows.append(
            f'<div style="margin:4px 0"><span style="display:inline-block;'
            f'width:140px;color:#e2e8f0;font-size:13px">{safe_name}</span>'
            f'<span style="display:inline-block;background:#ef4444;'
            f'border-radius:3px;width:{w}px;height:14px;vertical-align:middle"></span>'
            f'<span style="margin-left:6px;color:#94a3b8;font-size:13px">{cnt}</span></div>')
    return "".join(rows)


def build_report_html(report: dict, title: str = "缺陷检测报告") -> str:
    """report: database.export_report 的返回 dict"""
    total, ng, ok = report["count"], report["ng"], report["ok"]
    yld = report["yield"]
    dist = report["defect_dist"]
    ng_paths = report["ng_paths"]

    kpis = f"""
    <div style="display:flex;gap:16px;flex-wrap:wrap">
      <div style="background:#0b1120;border:1px solid #1e293b;border-radius:8px;
                  padding:14px 22px;text-align:center">
        <div style="color:#94a3b8;font-size:13px">总数</div>
        <div style="color:#f1f5f9;font-size:26px;font-weight:600">{total}</div></div>
      <div style="background:#0b1120;border:1px solid #1e293b;border-radius:8px;
                  padding:14px 22px;text-align:center">
        <div style="color:#94a3b8;font-size:13px">OK</div>
        <div style="color:#22c55e;font-size:26px;font-weight:600">{ok}</div></div>
      <div style="background:#0b1120;border:1px solid #1e293b;border-radius:8px;
                  padding:14px 22px;text-align:center">
        <div style="color:#94a3b8;font-size:13px">NG</div>
        <div style="color:#ef4444;font-size:26px;font-weight:600">{ng}</div></div>
      <div style="background:#0b1120;border:1px solid #1e293b;border-radius:8px;
                  padding:14px 22px;text-align:center">
        <div style="color:#94a3b8;font-size:13px">良率</div>
        <div style="color:#f59e0b;font-size:26px;font-weight:600">{yld}%</div></div>
    </div>"""

    # NG 拼图网格（最多 12 张）
    grid = []
    shown = ng_paths[:12]
    for i, p in enumerate(shown):
        b64 = _read_thumbnail(p)
        if b64:
            inner = (f'<img src="data:image/jpeg;base64,{b64}" '
                     f'style="width:100%;height:100%;object-fit:cover;border-radius:6px">')
        else:
            inner = ('<div style="width:100%;height:100%;display:flex;align-items:center;'
                     'justify-content:center;color:#475569;font-size:12px">无图</div>')
        grid.append(
            f'<div style="width:140px;height:140px;background:#0b1120;'
            f'border:1px solid #1e293b;border-radius:8px;overflow:hidden">{inner}</div>')
    grid_html = "".join(grid) if grid else (
        "<p style='color:#94a3b8'>该时间段无 NG 图片</p>")
    ng_more = f"（仅显示前 {len(shown)} 张，共 {len(ng_paths)} 张）" if len(ng_paths) > 12 else (
        f"（共 {len(ng_paths)} 张）" if ng_paths else "")

    html = f"""<!DOCTYPE html>
<html lang="zh"><head><meta charset="utf-8">
<title>{_esc(title)}</title></head>
<body style="margin:0;background:#020617;font-family:'Microsoft YaHei',sans-serif;padding:28px">
  <h2 style="color:#f1f5f9;margin:0 0 6px">{_esc(title)}</h2>
  <p style="color:#94a3b8;font-size:13px;margin:0 0 20px">
    生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</p>
  {kpis}

  <h3 style="color:#e2e8f0;margin:26px 0 10px">缺陷类型分布</h3>
  <div style="background:#0b1120;border:1px solid #1e293b;border-radius:8px;padding:16px">
    {_defect_bar(dist)}</div>

  <h3 style="color:#e2e8f0;margin:26px 0 10px">NG 图片拼图 {ng_more}</h3>
  <div style="display:flex;gap:10px;flex-wrap:wrap">{grid_html}</div>
</body></html>"""
    return html


def save_report(path: str, report: dict, title: str = "缺陷检测报告") -> str:
    """生成并保存 HTML 报告，返回保存路径"""
    html = build_report_html(report, title)
    with open(path, "w", encoding="utf-8") as f:
        f.write(html)
    return path

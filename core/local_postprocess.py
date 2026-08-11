# -*- coding: utf-8 -*-
"""YOLOv8 ONNX 输出后处理（供本地推理 .onnx 使用）"""
import numpy as np


def yolo_postprocess(output, scale, pad_w, pad_h, conf_thres=0.25, iou_thres=0.45):
    """YOLOv8 输出 (1, 4+classes, 8400) 或 (1, 8400, 4+classes) → 目标列表

    返回: [{"box": [x1,y1,x2,y2], "confidence": f, "class_id": i, "result": "NG"}, ...]
    """
    pred = np.squeeze(output)
    if pred.ndim == 2 and pred.shape[0] in (84, 85, 10):
        pred = pred.transpose(1, 0)  # (8400, 4+classes)

    n_anchors, n_feat = pred.shape
    n_classes = n_feat - 4

    boxes = pred[:, :4]      # (N, 4) xywh
    scores = pred[:, 4:]     # (N, classes)

    # 每个 anchor 取最高分类别
    class_scores = scores.max(axis=1)
    class_ids = scores.argmax(axis=1)
    mask = class_scores > conf_thres
    if not mask.any():
        return []

    boxes = boxes[mask]
    class_ids = class_ids[mask]
    class_scores = class_scores[mask]

    # xywh → xyxy
    cx, cy, w, h = boxes[:, 0], boxes[:, 1], boxes[:, 2], boxes[:, 3]
    x1 = cx - w / 2
    y1 = cy - h / 2
    x2 = cx + w / 2
    y2 = cy + h / 2
    boxes_xyxy = np.stack([x1, y1, x2, y2], axis=1)

    # 坐标还原（letterbox 逆变换）
    boxes_xyxy[:, [0, 2]] = (boxes_xyxy[:, [0, 2]] - pad_w) / scale
    boxes_xyxy[:, [1, 3]] = (boxes_xyxy[:, [1, 3]] - pad_h) / scale

    # NMS
    keep = _nms(boxes_xyxy, class_scores, iou_thres)

    dets = []
    for idx in keep:
        box = boxes_xyxy[idx]
        dets.append({
            "box": [float(box[0]), float(box[1]), float(box[2]), float(box[3])],
            "confidence": float(class_scores[idx]),
            "class_id": int(class_ids[idx]),
            "result": "NG",
        })
    return dets


def _nms(boxes, scores, iou_thres):
    """普通 NMS（按分数降序）"""
    if len(boxes) == 0:
        return []
    x1, y1, x2, y2 = boxes[:, 0], boxes[:, 1], boxes[:, 2], boxes[:, 3]
    areas = (x2 - x1) * (y2 - y1)
    order = scores.argsort()[::-1]
    keep = []
    while order.size > 0:
        i = order[0]
        keep.append(i)
        if order.size == 1:
            break
        xx1 = np.maximum(x1[i], x1[order[1:]])
        yy1 = np.maximum(y1[i], y1[order[1:]])
        xx2 = np.minimum(x2[i], x2[order[1:]])
        yy2 = np.minimum(y2[i], y2[order[1:]])
        w = np.maximum(0.0, xx2 - xx1)
        h = np.maximum(0.0, yy2 - yy1)
        inter = w * h
        iou = inter / (areas[i] + areas[order[1:]] - inter + 1e-6)
        inds = np.where(iou <= iou_thres)[0]
        order = order[inds + 1]
    return keep

"""
通信调试脚本：上位机协议 → Nano 推理服务
协议：4 字节大端长度头 + UTF-8 JSON
测试项：心跳 / 模型列表 / 图片推理
"""
import base64
import json
import socket
import struct
import sys
import time

sys.path.insert(0, r"D:\RK3568&Orin Nano\ORIN NANO\Host PC")
from core.class_names import resolve_class_names

HOST = "<NANO_LAN_IP>"
PORT = 8888
IMAGE_PATH = r"D:\RK3568&Orin Nano\ORIN NANO\Model Training\NEU-DET-with-yolov8-main\data\NEU-DET\test\images\crazing_271.jpg"


def send(sock, payload: dict):
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    sock.sendall(struct.pack(">I", len(data)) + data)
    print(f"  -> {payload['type']} ({len(data)}B)")


def recv(sock, timeout=30):
    sock.settimeout(timeout)
    header = sock.recv(4)
    if not header:
        raise ConnectionError("连接已关闭")
    length = struct.unpack(">I", header)[0]
    body = b""
    while len(body) < length:
        chunk = sock.recv(length - len(body))
        if not chunk:
            raise ConnectionError("连接已关闭")
        body += chunk
    return json.loads(body.decode("utf-8"))


def main():
    print(f"=== 通信调试 {HOST}:{PORT} ===")
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.connect((HOST, PORT))
    print(f"[OK] TCP 已连接")

    # 1. 心跳
    print("\n--- 1. 心跳测试 ---")
    send(sock, {"type": "heartbeat", "timestamp": time.time()})
    r = recv(sock)
    print(f"  <- {r.get('type')} | ok={r.get('ok')} | keys={list(r.keys())}")

    # 2. 模型列表
    print("\n--- 2. 模型列表 ---")
    send(sock, {"type": "model_list_request"})
    r = recv(sock)
    print(f"  <- {r.get('type')} | ok={r.get('ok')}")
    models = r.get("models") or r.get("model_list") or []
    for m in models[:10]:
        name = m.get("name") if isinstance(m, dict) else m
        print(f"     - {name}")

    # 3. 图片推理
    print("\n--- 3. 图片推理 ---")
    with open(IMAGE_PATH, "rb") as f:
        img_b64 = base64.b64encode(f.read()).decode("ascii")
    print(f"  图片: {IMAGE_PATH.split(chr(92))[-1]} | base64 {len(img_b64)//1024}KB")
    send(sock, {"type": "detect_request",
                "image_base64": img_b64,
                "timestamp": time.time()})
    t0 = time.time()
    r = recv(sock, timeout=60)
    dt = time.time() - t0
    model_name = r.get("model", "")
    class_names = resolve_class_names(model_name)
    print(f"  <- {r.get('type')} | ok={r.get('ok')} | 端到端 {dt*1000:.1f}ms")
    timing = r.get("timing", {})
    if timing:
        print(f"     推理耗时: GPU={timing.get('gpu_inference_ms', 0):.1f}ms "
              f"总={timing.get('total_ms', 0):.1f}ms (模型: {model_name})")
    dets = r.get("detections") or []
    print(f"     检测框数: {len(dets)}")
    for d in dets[:8]:
        cid = d.get("class_id")
        cname = class_names[cid] if class_names and cid is not None and cid < len(class_names) else f"cls{cid}"
        print(f"      - [{cname}] (id={cid}) conf={d.get('confidence', 0):.3f} "
              f"box={[round(x, 1) for x in d.get('box', [])]} result={d.get('result')}")

    sock.close()
    print("\n=== 调试完成 ===")


if __name__ == "__main__":
    main()

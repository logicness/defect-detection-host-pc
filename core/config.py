"""
配置持久化：data/host_config.json
各页参数（TCP/PLC/相机/模型/存储/预处理）统一读写，重启不丢失
"""
import json
import os
import sys

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
CONFIG_PATH = os.path.join(DATA_DIR, "host_config.json")

DEFAULT_CONFIG = {
    "tcp": {"host": "<NANO_LAN_IP>", "port": 8888, "heartbeat": 5,
            "retries": 1, "timeout": 10},
    "plc": {"protocol": "Modbus TCP", "host": "<PLC_LAN_IP>", "port": 2000,
            "unit_id": 1, "timeout_ms": 3000, "poll_ms": 500},
    "camera": {"name": "相机 01", "exposure": 10.0, "gain": 2.0, "brightness": 128,
               "trigger": "连续触发", "fps": "30 FPS"},
    "detect": {"conf": 0.85, "min_area": 50, "model": "Product_A_v1",
               "model_file": "", "input_size": "640×640"},
    "preprocess": {"resize": "不缩放（保持原始）", "gray_norm": True,
                   "denoise": "中值滤波（3×3）", "contrast": True},
    "storage": {"save_path": "D:/Inspect/Images", "save_ng": True,
                "save_orig": True, "auto_clean": "磁盘空间 < 10% 时删除"},
    "state": {
        "last_local_model": "",      # 上次 PC 本地模型路径
        "last_image_dir": "",        # 上次图片选择目录
        "last_model_dir": "",        # 上次模型选择目录
        "last_nano_model": "",       # 上次 Nano 激活模型名
        "last_nano_image_dir": "",   # 上次下位机图片文件夹
    },
    "rois": [
        {"name": "ROI 1", "enabled": False, "x": 120, "y": 120, "w": 420, "h": 420},
        {"name": "ROI 2", "enabled": False, "x": 380, "y": 120, "w": 150, "h": 420},
    ],
}


def load_config() -> dict:
    """读取配置，缺失键用默认值补齐"""
    cfg = json.loads(json.dumps(DEFAULT_CONFIG))  # deep copy
    try:
        if os.path.isfile(CONFIG_PATH):
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                saved = json.load(f)
            for k, v in saved.items():
                if isinstance(v, dict) and isinstance(cfg.get(k), dict):
                    cfg[k].update(v)
                else:
                    cfg[k] = v
    except Exception:
        pass
    return cfg


def save_config(cfg: dict):
    """保存配置（原子写入 + 异常兜底，失败不抛错仅记录，避免主流程崩溃）"""
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
        # 原子写入：先写临时文件再替换，避免写一半损坏配置
        tmp = CONFIG_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
        os.replace(tmp, CONFIG_PATH)
    except Exception as e:
        # 文件被占用/权限问题等：不阻塞主流程（配置丢失可容忍，界面崩溃不可容忍）
        try:
            print(f"[config] 保存配置失败(忽略): {e}", file=sys.stderr)
        except Exception:
            pass

# -*- coding: utf-8 -*-
"""
告警推送（连续 NG → 企业微信 / Server酱）
=========================================
在 controller.ng_alarm 触发时推送告警到微信，产线异常实时感知。

配置：data/alarm_config.json（不存在则用默认值，可通过 UI 或手改配置）
{
  "enabled": false,
  "channel": "wecom",            # wecom=企业微信群机器人 / serverchan=Server酱
  "webhook": "",                 # 企业微信机器人 webhook 或 Server酱 SendKey
  "cooldown_min": 30,            # 同一轮告警冷却（分钟），避免轰炸
  "max_per_day": 50              # 每日上限，防止异常刷屏
}
"""
import json
import os
import time
import urllib.request
import urllib.error

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
CONFIG_PATH = os.path.join(DATA_DIR, "alarm_config.json")

DEFAULT_CONFIG = {
    "enabled": False,
    "channel": "wecom",
    "webhook": "",
    "cooldown_min": 30,
    "max_per_day": 50,
}


def load_config() -> dict:
    cfg = dict(DEFAULT_CONFIG)
    try:
        if os.path.isfile(CONFIG_PATH):
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                cfg.update(json.load(f))
    except Exception:
        pass
    return cfg


def save_config(cfg: dict) -> bool:
    """保存配置（原子写，失败静默）"""
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
        tmp = CONFIG_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
        os.replace(tmp, CONFIG_PATH)
        return True
    except Exception:
        return False


class AlarmPusher:
    """告警推送器：企业微信 / Server酱 双通道，带冷却+每日限额"""

    def __init__(self):
        self._cfg = load_config()
        self._last_push_ts = 0.0      # 上次推送时间（冷却用）
        self._day = ""                # 今日日期
        self._today_count = 0         # 今日已推次数
        self._stat = {"pushed": 0, "blocked": 0}

    def get_stats(self) -> dict:
        return dict(self._stat)

    def reload(self):
        self._cfg = load_config()

    def push(self, message: str) -> bool:
        """推送告警，返回是否成功发送"""
        cfg = self._cfg
        if not cfg.get("enabled") or not cfg.get("webhook"):
            return False

        # 每日限额
        today = time.strftime("%Y-%m-%d")
        if today != self._day:
            self._day = today
            self._today_count = 0
        if self._today_count >= cfg.get("max_per_day", 50):
            self._stat["blocked"] += 1
            return False

        # 冷却
        cooldown = cfg.get("cooldown_min", 30) * 60
        if time.time() - self._last_push_ts < cooldown:
            self._stat["blocked"] += 1
            return False

        channel = cfg.get("channel", "wecom")
        ok = False
        try:
            if channel == "wecom":
                ok = self._push_wecom(cfg["webhook"], message)
            else:
                ok = self._push_serverchan(cfg["webhook"], message)
        except Exception:
            ok = False

        if ok:
            self._last_push_ts = time.time()
            self._today_count += 1
            self._stat["pushed"] += 1
        else:
            self._stat["blocked"] += 1
        return ok

    # ---------------- 通道实现 ----------------
    @staticmethod
    def _push_wecom(webhook: str, message: str) -> bool:
        """企业微信群机器人（text 类型）"""
        payload = json.dumps({"msgtype": "text", "text": {"content": message}}).encode("utf-8")
        req = urllib.request.Request(webhook, data=payload,
                                     headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                r = json.loads(resp.read().decode("utf-8"))
                return r.get("errcode") == 0
        except (urllib.error.URLError, OSError, ValueError):
            return False

    @staticmethod
    def _push_serverchan(sendkey: str, message: str) -> bool:
        """Server酱（title=缺陷告警, desp=详情）"""
        import urllib.parse
        url = f"https://sctapi.ftqq.com/{sendkey}.send"
        data = urllib.parse.urlencode({"title": "缺陷检测告警", "desp": message}).encode("utf-8")
        req = urllib.request.Request(url, data=data)
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                r = json.loads(resp.read().decode("utf-8"))
                return r.get("code") == 0
        except (urllib.error.URLError, OSError, ValueError):
            return False


# 单例（供 controller/main 复用）
_alarm_pusher = None


def get_pusher() -> AlarmPusher:
    global _alarm_pusher
    if _alarm_pusher is None:
        _alarm_pusher = AlarmPusher()
    return _alarm_pusher

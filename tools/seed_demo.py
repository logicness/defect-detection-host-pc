"""
演示数据生成：向 data/defect_host.db 写入 1000 条检测记录
（995 OK + 5 NG，良率 99.5%，对齐设计稿），时间分布最近 24h
用法: python tools/seed_demo.py
"""
import os
import random
import sys
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.database import DatabaseManager  # noqa: E402

NEU = ["crazing", "inclusion", "patches",
       "pitted_surface", "rolled-in_scale", "scratches"]


def main():
    db = DatabaseManager()
    now = datetime.now()
    ng_slots = set(random.sample(range(1000), 5))
    for i in range(1000):
        ts = (now - timedelta(seconds=(1000 - i) * 2)).strftime("%Y-%m-%d %H:%M:%S")
        path = f"C:/data/images/sample_{i + 1:04d}.png"
        if i in ng_slots:
            cls = random.choice(NEU)
            conf = round(random.uniform(0.86, 0.95), 2)
            x1, y1 = random.randint(300, 450), random.randint(200, 300)
            w, h = random.randint(40, 90), random.randint(40, 90)
            db.insert_record(defect_type=cls, confidence=conf,
                             bbox=(x1, y1, x1 + w, y1 + h), area=w * h,
                             image_path=path, result="缺陷", timestamp=ts)
        else:
            db.insert_record(defect_type="none", confidence=1.0,
                             image_path=path, result="合格", timestamp=ts)
        day = ts[:10]
        db.update_stats(date=day, defect_count=1 if i in ng_slots else 0,
                        pass_count=0 if i in ng_slots else 1,
                        avg_conf=0.9, avg_fps=30)
    kpi = db.get_kpi()
    print(f"已写入 1000 条演示数据: 总{kpi['total']} OK{kpi['ok']} "
          f"NG{kpi['ng']} 良率{kpi['yield']:.1f}%")
    db.close()


if __name__ == "__main__":
    main()

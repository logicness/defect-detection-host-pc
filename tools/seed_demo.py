"""
演示数据生成：向独立演示库写入 1000 条检测记录
（995 OK + 5 NG，良率 99.5%，对齐设计稿），时间分布最近 24h

⚠️ 默认写入 data/demo_defect.db（独立演示库），绝不触碰生产 defect_host.db。
用法: python tools/seed_demo.py [--db 路径] [--yes]
"""
import argparse
import os
import random
import sys
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.database import DatabaseManager  # noqa: E402

NEU = ["crazing", "inclusion", "patches",
       "pitted_surface", "rolled-in_scale", "scratches"]

DEMO_DB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                       "data", "demo_defect.db")


def main():
    parser = argparse.ArgumentParser(description="生成演示检测数据")
    parser.add_argument("--db", default=DEMO_DB, help="目标演示库路径（默认 data/demo_defect.db）")
    parser.add_argument("--yes", action="store_true", help="跳过确认（默认交互确认）")
    args = parser.parse_args()

    db_path = os.path.abspath(args.db)
    # 防呆：绝不写生产库
    if os.path.normpath(db_path).replace("\\", "/").endswith("data/defect_host.db"):
        print("[错误] 禁止写入生产库 defect_host.db，演示数据请用独立库（--db）")
        sys.exit(1)
    if os.path.exists(db_path) and not args.yes:
        r = input(f"目标库已存在: {db_path}\n将追加 1000 条演示数据，继续? [y/N] ")
        if r.strip().lower() not in ("y", "yes"):
            print("已取消")
            sys.exit(0)

    db = DatabaseManager(db_path)
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
    print(f"已写入 1000 条演示数据到 {db_path}: 总{kpi['total']} OK{kpi['ok']} "
          f"NG{kpi['ng']} 良率{kpi['yield']:.1f}%")
    db.close()


if __name__ == "__main__":
    main()

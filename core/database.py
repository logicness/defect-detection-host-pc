"""
SQLite 数据库：检测记录 + 生产统计
线程安全（锁 + check_same_thread=False），供异步写库线程与 UI 查询共用
"""
import csv
import os
import sqlite3
import threading
from datetime import datetime

from PyQt5.QtCore import QObject, pyqtSignal

from core.config import DATA_DIR

DB_PATH = os.path.join(DATA_DIR, "defect_host.db")


class DatabaseManager(QObject):
    log_message = pyqtSignal(str, str)  # (level, msg)

    def __init__(self, path: str = DB_PATH):
        super().__init__()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        self._lock = threading.RLock()
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self._create_tables()

    def _create_tables(self):
        with self._lock, self.conn:
            self.conn.execute("""
                CREATE TABLE IF NOT EXISTS defect_records (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT, product TEXT, result TEXT,
                    defect_type TEXT, confidence REAL, area INTEGER,
                    bbox TEXT, image_path TEXT
                )""")
            self.conn.execute("""
                CREATE TABLE IF NOT EXISTS production_stats (
                    date TEXT PRIMARY KEY, total INTEGER DEFAULT 0,
                    defect INTEGER DEFAULT 0, pass INTEGER DEFAULT 0,
                    avg_conf REAL DEFAULT 0, avg_fps REAL DEFAULT 0
                )""")

    # ---------------- 写入 ----------------
    def insert_record(self, defect_type: str = "none", confidence: float = 0,
                      bbox=None, image_path: str = "", result: str = "合格",
                      product: str = "Product_A_v1", area: int = 0,
                      timestamp: str = ""):
        ts = timestamp or datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with self._lock, self.conn:
            self.conn.execute(
                "INSERT INTO defect_records (timestamp, product, result, defect_type,"
                " confidence, area, bbox, image_path) VALUES (?,?,?,?,?,?,?,?)",
                (ts, product, result, defect_type, confidence, area,
                 ",".join(map(str, bbox)) if bbox else "", image_path))

    def update_stats(self, date: str, defect_count: int, pass_count: int,
                     avg_conf: float = 0, avg_fps: float = 0):
        with self._lock, self.conn:
            self.conn.execute(
                "INSERT INTO production_stats (date, total, defect, pass, avg_conf, avg_fps)"
                " VALUES (?,1,?,?,?,?) ON CONFLICT(date) DO UPDATE SET"
                " total=total+1, defect=defect+?, pass=pass+?, avg_conf=?, avg_fps=?",
                (date, defect_count, pass_count, avg_conf, avg_fps,
                 defect_count, pass_count, avg_conf, avg_fps))

    # ---------------- 查询 ----------------
    @staticmethod
    def _where(date_from="", date_to="", product="全部", result="全部",
               defect_type="全部", keyword=""):
        conds, args = [], []
        if date_from:
            conds.append("timestamp >= ?"); args.append(date_from)
        if date_to:
            conds.append("timestamp <= ?"); args.append(date_to)
        if product and product != "全部":
            conds.append("product = ?"); args.append(product)
        if result and result != "全部":
            conds.append("result = ?")
            args.append("缺陷" if result == "NG" else "合格")
        if defect_type and defect_type != "全部":
            conds.append("defect_type = ?"); args.append(defect_type)
        if keyword:
            conds.append("image_path LIKE ?"); args.append(f"%{keyword}%")
        where = " WHERE " + " AND ".join(conds) if conds else ""
        return where, args

    def query_records(self, limit: int = 1000, offset: int = 0, **filters) -> list:
        where, args = self._where(**filters)
        sql = ("SELECT * FROM defect_records" + where +
               " ORDER BY id DESC LIMIT ? OFFSET ?")
        with self._lock:
            rows = self.conn.execute(sql, args + [limit, offset]).fetchall()
        return [dict(r) for r in rows]

    def count_records(self, **filters) -> int:
        where, args = self._where(**filters)
        with self._lock:
            return self.conn.execute(
                "SELECT COUNT(*) FROM defect_records" + where, args).fetchone()[0]

    def get_kpi(self, **filters) -> dict:
        """总数 / OK / NG / 良率"""
        where, args = self._where(**filters)
        with self._lock:
            total = self.conn.execute(
                "SELECT COUNT(*) FROM defect_records" + where, args).fetchone()[0]
            ng = self.conn.execute(
                "SELECT COUNT(*) FROM defect_records" + where +
                (" AND" if where else " WHERE ") + " result='缺陷'", args).fetchone()[0]
        ok = total - ng
        yield_rate = (ok / total * 100) if total else 0.0
        return {"total": total, "ok": ok, "ng": ng, "yield": yield_rate}

    def get_defect_types(self) -> list:
        with self._lock:
            rows = self.conn.execute(
                "SELECT DISTINCT defect_type FROM defect_records"
                " WHERE result='缺陷' ORDER BY defect_type").fetchall()
        return [r[0] for r in rows]

    # ---------------- 统计图表（A4） ----------------
    def get_daily_stats(self, days: int = 7) -> list:
        """趋势图数据：按天聚合 total/defect/pass（最近 days 天）
        返回 [{"date":"YYYY-MM-DD","total":n,"defect":n,"pass":n}, ...]（升序）
        """
        from datetime import timedelta
        start = (datetime.now() - timedelta(days=days - 1)).strftime("%Y-%m-%d")
        sql = """SELECT substr(timestamp,1,10) as date,
                        COUNT(*) as total,
                        SUM(CASE WHEN result='缺陷' THEN 1 ELSE 0 END) as defect
                 FROM defect_records
                 WHERE substr(timestamp,1,10) >= ?
                 GROUP BY substr(timestamp,1,10)
                 ORDER BY date"""
        with self._lock:
            rows = self.conn.execute(sql, (start,)).fetchall()
        data = [dict(r) for r in rows]
        for r in data:
            r["pass"] = r["total"] - r["defect"]
        return data

    def get_defect_type_stats(self) -> list:
        """缺陷类型统计：按类型聚合 count/avg_conf"""
        sql = """SELECT defect_type, COUNT(*) as count, AVG(confidence) as avg_conf
                 FROM defect_records
                 WHERE result='缺陷'
                 GROUP BY defect_type ORDER BY count DESC"""
        with self._lock:
            rows = self.conn.execute(sql).fetchall()
        return [dict(r) for r in rows]

    # ---------------- 导出/清理 ----------------
    def export_csv(self, path: str, **filters) -> int:
        rows = self.query_records(limit=1000000, **filters)
        with open(path, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.writer(f)
            w.writerow(["时间", "产品", "结果", "缺陷类型", "置信度", "面积", "图像路径"])
            for r in rows:
                w.writerow([r["timestamp"], r["product"], r["result"],
                            r["defect_type"], r["confidence"], r["area"], r["image_path"]])
        return len(rows)

    def clean_old_records(self, keep_days: int = 30) -> int:
        from datetime import timedelta
        cutoff = (datetime.now() - timedelta(days=keep_days)).strftime("%Y-%m-%d %H:%M:%S")
        with self._lock, self.conn:
            cur = self.conn.execute(
                "DELETE FROM defect_records WHERE timestamp < ?", (cutoff,))
        return cur.rowcount

    def clear_all_records(self) -> int:
        """清空所有检测记录与生产统计（慎用）"""
        with self._lock, self.conn:
            cur1 = self.conn.execute("DELETE FROM defect_records")
            cur2 = self.conn.execute("DELETE FROM production_stats")
        return cur1.rowcount + cur2.rowcount

    def close(self):
        with self._lock:
            self.conn.close()

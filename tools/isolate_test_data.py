# -*- coding: utf-8 -*-
"""测试数据隔离（必须在 import main/core 之前调用 setup）

问题背景：tools 下的测试/演示脚本（smoke_test / test_no_sim_start / ui_walkthrough /
_test_stop_*）会通过 controller 结果管线把模拟/真实推理结果写入**生产** defect_host.db、
覆盖 host_config.json、写入 D:/Inspect/Images NG 图。2026-08-20 已发生一次真实污染。

用法：
    sys.path.insert(0, os.path.join(HOST_ROOT, "tools"))
    import isolate_test_data as iso
    TMP = iso.setup()                 # 在 import main 之前
    win = MainWindow()
    iso.isolate_ng_saver(win)         # 构建窗口后：NG 图也重定向到临时目录
"""
import os
import sys
import tempfile

TMP_DIR = ""


def setup() -> str:
    """把 core.config.DATA_DIR / core.database.DB_PATH 重定向到临时目录。
    必须在 import main / core.database 之前调用（模块级 DB_PATH 在导入时计算）。"""
    global TMP_DIR
    TMP_DIR = tempfile.mkdtemp(prefix="host_test_data_")

    # 注意顺序：core.database 在导入时执行 `from core.config import DATA_DIR`，
    # 必须先改 core.config 再让 database 模块被首次导入。
    import core.config as _cfg
    _cfg.DATA_DIR = TMP_DIR
    _cfg.CONFIG_PATH = os.path.join(TMP_DIR, "host_config.json")

    import core.database as _db
    _db.DB_PATH = os.path.join(TMP_DIR, "defect_host.db")
    return TMP_DIR


def isolate_ng_saver(win):
    """NG 图保存重定向到临时目录（防写生产 D:/Inspect/Images）"""
    try:
        if TMP_DIR:
            win.ng_saver.set_save_root(os.path.join(TMP_DIR, "images"))
    except Exception:
        pass
